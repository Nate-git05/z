"""CLI helpers for z skill add / create / list / show / reindex."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

from .generate import generate_skill
from .grounding import GroundingPack, make_ungrounded_skill_node
from .infer import apply_inferred_metadata
from .remote import sync_skill
from .router import normalize_repo_key
from .schema import Skill
from .session import format_skill_metadata, print_skills_list
from .store import LocalSkillStore
from .vector import get_skill_vector_index, upsert_skill_vector


def _stamp_repo_key(skill: Skill, *, root: Optional[Path | str] = None, shared: bool = False) -> None:
    """Bind skill to the current project unless explicitly shared.

    Bug-pattern skills default to ``shared=True`` / empty ``repo_key`` — they
    are symptom-based and portable across codebases. Feature playbooks/scaffolds
    stay repo-bound (schema rule: empty + shared=True → apply anywhere).
    """
    from .schema import SKILL_KIND_BUG_PATTERN

    if shared or (skill.kind or "") == SKILL_KIND_BUG_PATTERN:
        skill.shared = True
        skill.repo_key = ""
        return
    if skill.shared:
        return
    if skill.repo_key:
        return
    key = normalize_repo_key(root or Path.cwd())
    if key:
        skill.repo_key = key


def _created_by() -> Optional[str]:
    try:
        from aider.z.auth import current_session

        creds = current_session()
        if creds:
            return creds.display_name()
    except Exception:
        pass
    return None


def _persist_skill(io, skill: Skill, *, sync: bool = True) -> Skill:
    store = LocalSkillStore()
    apply_inferred_metadata(skill, source=skill.source)
    path = store.save(skill)
    skill.path = str(path)
    # Rewrite frontmatter with final path
    store.save(skill)
    upsert_skill_vector(skill)
    io.tool_output(f"Saved skill: {skill.title}")
    io.tool_output(f"  → {skill.path}")

    if sync:
        remote_id = sync_skill(skill)
        if remote_id:
            skill.remote_id = remote_id
            store.save(skill)
            upsert_skill_vector(skill)
            io.tool_output("  Synced to workspace/account (manage at /app/skills).")
        else:
            try:
                from aider.z.auth import current_session

                if current_session():
                    io.tool_warning("Could not sync to server; skill kept locally.")
            except Exception:
                pass
    return skill


def cmd_skill_add(io, content: str = "", *, sync: bool = True) -> int:
    """Paste/import a skill body. Z infers metadata and indexes in ChromaDB."""
    content = (content or "").strip()
    if not content:
        io.tool_output("Paste the skill markdown, then finish with a line containing only END")
        io.tool_output("(or press Enter on an empty prompt to cancel).")
        lines = []
        while True:
            line = io.prompt_ask("")
            if line is None:
                break
            if line.strip() == "END":
                break
            if not line.strip() and not lines:
                break
            lines.append(line)
        content = "\n".join(lines).strip()

    if not content:
        io.tool_error("No skill content provided.")
        return 1

    # Allow pasting a full file with frontmatter
    from .store import skill_from_markdown

    parsed = skill_from_markdown(content)
    kwargs = dict(
        title=parsed.title if parsed.title != "Untitled skill" else "",
        description=parsed.description,
        content=parsed.content or content,
        tags=list(parsed.tags or []),
        project_types=list(parsed.project_types or []),
        triggers=list(parsed.triggers or []),
        source="paste",
        created_by=_created_by(),
        kind=parsed.kind or "playbook",
        symptom_description=parsed.symptom_description or "",
        root_cause_category=parsed.root_cause_category or "",
        root_cause_explanation=parsed.root_cause_explanation or "",
        fix_technique=parsed.fix_technique or "",
        verification_method=parsed.verification_method or "",
        language=parsed.language or "",
        languages=list(parsed.languages or []),
    )
    if parsed.id:
        kwargs["id"] = parsed.id
    skill = Skill(**kwargs)
    # Preserve explicit shared/repo_key from pasted frontmatter; else bind to cwd.
    # bug_pattern → always shared (portable) via _stamp_repo_key.
    if parsed.shared or (parsed.repo_key or "").strip() in ("*", "global", "any"):
        skill.shared = True
        skill.repo_key = ""
    elif (skill.kind or "") == "bug_pattern":
        _stamp_repo_key(skill)  # forces shared=True, repo_key=""
    elif parsed.repo_key:
        skill.repo_key = parsed.repo_key.strip()
    else:
        _stamp_repo_key(skill)

    _persist_skill(io, skill, sync=sync)
    return 0


def cmd_skill_create(
    io, topic: str = "", *, model_name: Optional[str] = None, sync: bool = True
) -> int:
    """Generate a skill from a prompt via BYOK model.

    Bug-fix topics (segfault/crash/race/leak/…) use the same
    ``task_is_bugfix_intent`` classifier as automatic capture, so manual
    ``z skill create`` also produces ``kind=bug_pattern`` with structured fields.
    """
    topic = (topic or "").strip()
    if not topic:
        topic = io.prompt_ask("What should this skill cover?").strip()
    if not topic:
        io.tool_error("A skill description is required.")
        return 1

    from .router import task_is_bugfix_intent

    prefer_bug = task_is_bugfix_intent(topic)
    if prefer_bug:
        io.tool_output(
            "Bug-fix topic detected — generating bug_pattern skill "
            "(symptom / root cause / fix / verification)…"
        )
    else:
        io.tool_output("Generating skill with your connected model…")

    # Prefer grounding against the live working tree when available (same
    # discipline as post-task capture). Failure is non-fatal.
    grounding_pack = None
    try:
        import subprocess
        from pathlib import Path

        from .grounding import build_grounding_pack

        root = Path.cwd()
        dirty: list[str] = []
        diff = ""
        names = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=15,
        )
        if names.returncode == 0:
            dirty = [l.strip() for l in (names.stdout or "").splitlines() if l.strip()]
        body = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if body.returncode == 0:
            diff = body.stdout or ""
        if dirty or diff:
            grounding_pack = build_grounding_pack(
                user_request=topic,
                files_changed=dirty[:40],
                root=root,
                diff=diff[:20000],
            )
            if grounding_pack and not (grounding_pack.files or grounding_pack.diff):
                grounding_pack = None
    except Exception:
        grounding_pack = None

    skill, err, ground = generate_skill(
        topic,
        model_name=model_name,
        created_by=_created_by(),
        grounding_pack=grounding_pack,
        two_phase=bool(grounding_pack) and not prefer_bug,
        prefer_bug_pattern=prefer_bug,
    )
    if err or not skill:
        io.tool_error(err or "Skill generation failed.")
        return 1

    skill.source = "generate"
    if prefer_bug:
        from .schema import SKILL_KIND_BUG_PATTERN

        skill.kind = SKILL_KIND_BUG_PATTERN
        if ground and not ground.ok:
            skill.needs_review = True
            skill.quality_state = "draft"
            io.tool_warning(
                "Bug-pattern skill saved as draft — diagnosis may not match "
                "the real diff. Accept after review: z skill accept <name>"
            )
            if ground.reason:
                io.tool_warning(f"  {ground.reason}")
    # bug_pattern → shared/portable; feature skills → repo-bound
    _stamp_repo_key(skill, shared=prefer_bug)
    if prefer_bug:
        io.tool_output("  Scope: shared (bug-pattern — portable across projects)")
    _persist_skill(io, skill, sync=sync)
    return 0


def cmd_skill_list(io) -> int:
    print_skills_list(io)
    return 0


def cmd_skill_show(io, name: str = "") -> int:
    """Show skill metadata (and optionally full body)."""
    name = (name or "").strip()
    if not name:
        name = io.prompt_ask("Skill name or id").strip()
    if not name:
        io.tool_error("Skill name or id required.")
        return 1

    store = LocalSkillStore()
    skill = store.get(name)
    if not skill:
        # fuzzy title contains
        matches = [s for s in store.list_skills() if name.lower() in s.title.lower()]
        if len(matches) == 1:
            skill = matches[0]
        elif len(matches) > 1:
            io.tool_output("Multiple matches:")
            for s in matches[:10]:
                io.tool_output(f"  • {s.title} ({s.id[:8]})")
            return 1
    if not skill:
        io.tool_error(f"No skill found for “{name}”.")
        return 1

    io.tool_output(format_skill_metadata(skill))
    if io.confirm_ask("Open full skill body?", default="n"):
        io.tool_output("")
        io.tool_output(skill.content.strip())
    return 0


def cmd_skill_reindex(io) -> int:
    store = LocalSkillStore()
    skills = store.list_skills()
    try:
        index = get_skill_vector_index()
        if not index.available:
            io.tool_error("chromadb is not installed. pip install chromadb")
            return 1
        n = index.reindex(skills)
        io.tool_output(f"Reindexed {n} skill(s) into ChromaDB.")
        return 0
    except Exception as err:
        io.tool_error(f"Reindex failed: {err}")
        return 1


def save_skill_from_task(
    io,
    topic: str,
    *,
    context: str = "",
    model_name: Optional[str] = None,
    grounding_pack: Optional[GroundingPack] = None,
    uncertainty_engine=None,
    repo_root: Optional[Path | str] = None,
    prefer_bug_pattern: bool = False,
) -> Tuple[Optional[Skill], bool]:
    """
    Capture a skill after a completed task, grounded in diff/file evidence.

    Returns (skill, created). Skills that fail the grounding check are still
    saved with needs_review=True (blocked from auto-retrieve) and an uncertainty
    node is attached when an engine is available.
    Caller handles the "want to see metadata?" prompt.
    """
    label = (
        "Generating bug-pattern skill from the fix…"
        if prefer_bug_pattern
        else "Generating skill from changed files…"
    )
    io.tool_output(label)
    skill, err, ground = generate_skill(
        topic,
        model_name=model_name,
        context=context,
        created_by=_created_by(),
        grounding_pack=grounding_pack,
        two_phase=bool(grounding_pack) and not prefer_bug_pattern,
        prefer_bug_pattern=prefer_bug_pattern,
    )
    if err or not skill:
        io.tool_error(err or "Skill generation failed.")
        return None, False

    skill.source = "capture"
    # Captures always start as draft — accept after review (even if grounded)
    skill.quality_state = "draft"
    skill.needs_review = True
    if prefer_bug_pattern:
        from .schema import SKILL_KIND_BUG_PATTERN

        skill.kind = SKILL_KIND_BUG_PATTERN
    # bug_pattern → shared/portable by default (cross-project retrieval);
    # ordinary feature captures stay bound to repo_root.
    _stamp_repo_key(
        skill,
        root=repo_root,
        shared=prefer_bug_pattern or (skill.kind or "") == "bug_pattern",
    )
    if skill.shared and (skill.kind or "") == "bug_pattern":
        io.tool_output("  Scope: shared (bug-pattern — portable across projects)")
    if ground and not ground.ok:
        io.tool_warning(
            "Skill saved as draft — it may not match the real implementation."
        )
        if ground.reason:
            io.tool_warning(f"  {ground.reason}")
        _emit_ungrounded_node(io, skill, ground, uncertainty_engine)
    elif ground and ground.grounded_symbols:
        io.tool_output(
            "Grounded symbols: " + ", ".join(ground.grounded_symbols[:8])
        )
    io.tool_output(
        "Saved as draft (not auto-applied). Accept with: z skill accept <name>"
    )

    skill = _persist_skill(io, skill, sync=True)
    return skill, True


def _resolve_skill_for_state_change(io, store: LocalSkillStore, name: str):
    """Resolve accept/reject targets; surface title collisions instead of silent pick."""
    skill, candidates = store.resolve_by_name(name)
    if skill is None and candidates:
        io.tool_error(
            f"Multiple skills share the title “{name}” — specify one by id:"
        )
        for c in candidates:
            io.tool_output(
                f"  {c.id[:8]}  [{c.quality_state}]  {c.title}"
            )
        return None
    if skill is None:
        io.tool_error(f"No skill found for “{name}”.")
        return None
    if len(candidates) > 1:
        io.tool_output(
            f"Note: {len(candidates)} skills share this title — "
            f"resolved to the draft one ({skill.id[:8]})."
        )
    return skill


def accept_skill(io, name: str = "") -> int:
    """Promote a draft skill to verified so it can auto-apply."""
    name = (name or "").strip()
    if not name:
        name = io.prompt_ask("Skill name or id to accept").strip()
    if not name:
        io.tool_error("Skill name or id required.")
        return 1
    store = LocalSkillStore()
    skill = _resolve_skill_for_state_change(io, store, name)
    if not skill:
        return 1
    if (skill.quality_state or "") == "verified" and not skill.needs_review:
        io.tool_output(f"“{skill.title}” is already verified.")
        return 0
    # Before flipping to verified: if this draft failed evidence grounding,
    # confirm candidate taxonomy terms from the recorded miss blob (human
    # still owns any edit to bug_concepts.py via `z taxonomy review`).
    if getattr(skill, "grounding_miss_reason", None):
        try:
            from .taxonomy_candidates import (
                latest_miss_for_skill,
                record_confirmation_candidate,
            )

            miss = latest_miss_for_skill(skill.id)
            blob = (miss or {}).get("added_diff_blob") or ""
            recorded = record_confirmation_candidate(
                skill.root_cause_category or "",
                blob,
                skill.id,
                skill_title=skill.title or "",
            )
            if recorded:
                io.tool_output(
                    "Taxonomy candidates noted (review with: z taxonomy review): "
                    + ", ".join(recorded[:12])
                )
        except Exception:
            pass
    skill.quality_state = "verified"
    skill.needs_review = False
    store.save(skill)
    upsert_skill_vector(skill)
    io.tool_output(f"Accepted skill: {skill.title}")
    io.tool_output("It can now auto-apply on matching tasks.")
    return 0


def reject_skill(io, name: str = "") -> int:
    """Quarantine a skill so it never auto-applies."""
    name = (name or "").strip()
    if not name:
        name = io.prompt_ask("Skill name or id to reject").strip()
    if not name:
        io.tool_error("Skill name or id required.")
        return 1
    store = LocalSkillStore()
    skill = _resolve_skill_for_state_change(io, store, name)
    if not skill:
        return 1
    skill.quality_state = "rejected"
    skill.needs_review = True
    store.save(skill)
    upsert_skill_vector(skill)
    io.tool_output(f"Rejected (quarantined) skill: {skill.title}")
    return 0


def _emit_ungrounded_node(io, skill: Skill, ground, uncertainty_engine) -> None:
    try:
        node = make_ungrounded_skill_node(
            skill_title=skill.title,
            missing_symbols=getattr(ground, "missing_symbols", None) or [],
            source_files=skill.source_files,
            reason=getattr(ground, "reason", "") or "",
        )
        engine = uncertainty_engine
        if engine is not None and hasattr(engine, "store"):
            engine.store.add(node)
        elif engine is not None and hasattr(engine, "add"):
            engine.add(node)
        io.tool_output(
            "Uncertainty: skill may invent APIs — review before relying on it."
        )
    except Exception:
        pass


def offer_view_new_skill(io, skill: Skill) -> None:
    """Ask whether to show metadata for a newly captured skill."""
    if skill.needs_review:
        io.tool_output("This skill needs review before it will auto-apply.")
    if not io.confirm_ask("Want to see the new skill?", default="n"):
        return
    io.tool_output("")
    io.tool_output(format_skill_metadata(skill))
