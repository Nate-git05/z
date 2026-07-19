"""Generate skill content via the user's connected (BYOK) model."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional, Tuple

from .grounding import (
    GroundingPack,
    GroundingResult,
    check_grounding,
    format_grounding_pack,
)
from .infer import apply_inferred_metadata
from .schema import (
    SKILL_KIND_BUG_PATTERN,
    VALID_SKILL_KINDS,
    Skill,
    _as_str_list,
)

# Raised from the old 6000-char cap so grounding packs (diff + files) fit.
DEFAULT_CONTEXT_BUDGET = 28000

SKILL_SYSTEM = """You generate reusable coding-agent skills for ONE capability inside a project
(e.g. Stripe checkout, rate-limit middleware) — not an entire application.

HARD RULES for grounded capture (when evidence is provided below):
- Document ONLY classes, functions, methods, and files that appear in the evidence.
- Do NOT invent alternate algorithms or APIs (e.g. do not write TokenBucket if the
  code shows SlidingWindowRateLimiter).
- Prefer real symbol names from the "Symbols present" list and file contents.
- If evidence is thin, write a short convention playbook without inventing types.

Respond with ONLY a JSON object (no markdown fences) with keys:
  "title": short plain-language title (max ~80 chars)
  "description": one sentence describing when to apply this skill
  "content": markdown body with clear steps, conventions, and examples
  "capability": optional short label for the reusable capability
  "tags": optional array of short keywords
  "triggers": optional array of words/phrases that should activate this skill
  "project_types": optional array from [api, backend, frontend, mobile, infra, data, general]
  "kind": "scaffold" for one-shot project bootstrap, or "playbook" for ongoing reusable guidance
  "languages": optional array like ["go","python","typescript"]
  "artifacts": optional array of files/dirs that mean the scaffold is done (e.g. ["go.mod","main.go"])
"""

BUG_PATTERN_SYSTEM = """You generate a reusable BUG-PATTERN skill from a real, grounded fix.

This is NOT a feature playbook. Capture a transferable diagnosis:
  symptom (abstract) → root-cause category → fix technique → how it was verified.

HARD RULES:
- symptom_description must be abstract enough to transfer across codebases/languages.
  Do NOT name the specific repo; describe how the bug *presented*.
- root_cause_category MUST be one of the curated taxonomy ids provided below.
- root_cause_explanation must be grounded in the actual diff evidence — not speculation.
- fix_technique describes the general technique abstractly (transferable).
- verification_method says what actually confirmed the fix (e.g. ThreadSanitizer
  before/after), not "tests passed".
- Do NOT invent APIs or symbols absent from the evidence.

Respond with ONLY a JSON object (no markdown fences) with keys:
  "title": short title (max ~80 chars)
  "description": one sentence when this pattern applies
  "content": markdown with Symptom / Root cause / Fix technique / Verification sections
  "kind": "bug_pattern"
  "symptom_description": abstract transferable symptom text (THIS is what gets embedded)
  "root_cause_category": one curated taxonomy id from the list below
  "root_cause_explanation": grounded diagnosis tied to the real diff
  "fix_technique": abstract transferable fix technique
  "verification_method": what confirmed the fix
  "language": coarse language filter (cpp|c|go|rust|python|java|javascript|typescript)
  "tags": optional keywords
  "triggers": optional activation phrases
"""

EXTRACT_SYSTEM = """You extract facts from coding-task evidence for a skill document.
Respond with ONLY a JSON object (no markdown fences) with keys:
  "capability": short reusable capability name (not the whole app)
  "symbols": array of class/function/method names that ACTUALLY appear in the evidence
  "files": array of file paths from the evidence
  "steps": array of short factual steps describing what the code does
  "pitfalls": optional array of pitfalls visible from the evidence
Do not invent symbols. If a name is not in the evidence, omit it.
"""


def _extract_json(text: str) -> Optional[dict]:
    """Pull a JSON object from a model response (shared hardened extractor)."""
    from aider.z.llm_json import extract_json_from_response

    return extract_json_from_response(text)


def resolve_model(model_name: Optional[str] = None):
    """Resolve the user's BYOK model (same selection path as the agent)."""
    from aider.models import Model

    name = model_name
    if not name:
        # Prefer env override used by aider, else default
        import os

        name = os.environ.get("AIDER_MODEL") or os.environ.get("Z_MODEL")
    if not name:
        from aider.models import DEFAULT_MODEL_NAME

        name = DEFAULT_MODEL_NAME
    return Model(name)


def _call_model(model, system: str, user: str) -> Tuple[Optional[str], Optional[str]]:
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    try:
        raw = model.simple_send_with_retries(messages)
    except Exception as err:
        return None, f"Model call failed: {err}"
    if not raw:
        return None, "Model returned an empty response. Check your API key / model."
    return raw, None


def _extract_phase(model, pack_text: str, topic: str) -> Optional[dict]:
    user = (
        f"Topic: {topic}\n\n"
        "Extract only facts supported by this evidence:\n\n"
        f"{pack_text}\n"
    )
    raw, err = _call_model(model, EXTRACT_SYSTEM, user)
    if err or not raw:
        return None
    return _extract_json(raw)


def _skill_from_data(
    data: dict,
    topic: str,
    *,
    created_by: Optional[str],
    source: str,
    raw_fallback: str = "",
) -> Optional[Skill]:
    if not data:
        if not raw_fallback.strip():
            return None
        return Skill(
            title=topic[:80],
            description=f"Skill generated for: {topic[:120]}",
            content=raw_fallback.strip(),
            created_by=created_by,
            source=source,
        )

    title = (data.get("title") or topic)[:120].strip()
    description = (data.get("description") or topic)[:400].strip()
    content = (data.get("content") or "").strip()
    if not content:
        return None
    skill = Skill(
        title=title,
        description=description,
        content=content,
        created_by=created_by,
        tags=_as_str_list(data.get("tags")),
        triggers=_as_str_list(data.get("triggers")),
        project_types=_as_str_list(data.get("project_types")),
        source=source,
        capability=(data.get("capability") or "").strip()[:120],
    )
    # Optional router fields from model
    if data.get("kind"):
        kind = str(data.get("kind")).strip().lower() or skill.kind
        if kind in VALID_SKILL_KINDS:
            skill.kind = kind
    skill.languages = _as_str_list(data.get("languages")) or skill.languages
    skill.artifacts = _as_str_list(data.get("artifacts")) or skill.artifacts
    # bug_pattern fields
    skill.symptom_description = (data.get("symptom_description") or "").strip()
    skill.root_cause_category = (data.get("root_cause_category") or "").strip()
    skill.root_cause_explanation = (data.get("root_cause_explanation") or "").strip()
    skill.fix_technique = (data.get("fix_technique") or "").strip()
    skill.verification_method = (data.get("verification_method") or "").strip()
    language = (data.get("language") or "").strip().lower()
    if language:
        skill.language = language
        if language not in (skill.languages or []):
            skill.languages = [language] + list(skill.languages or [])
    if skill.kind == SKILL_KIND_BUG_PATTERN and skill.symptom_description:
        # Prefer symptom as the human-facing description when model omitted it
        if not skill.description or skill.description == topic:
            skill.description = skill.symptom_description[:400]
    return skill


def _apply_pack_metadata(skill: Skill, pack: GroundingPack, result: GroundingResult) -> None:
    skill.source_files = list(pack.source_files)
    grounded = list(result.grounded_symbols or pack.symbols[:40])
    # Never let taxonomy labels / schema field values pollute grounded_symbols
    if (skill.kind or "") == SKILL_KIND_BUG_PATTERN:
        from .bug_concepts import taxonomy_category_ids

        ban = {c.lower() for c in taxonomy_category_ids()}
        cat = (skill.root_cause_category or "").strip().lower()
        if cat:
            ban.add(cat)
        grounded = [
            s
            for s in grounded
            if (s or "").strip() and (s or "").strip().lower() not in ban
        ]
    skill.grounded_symbols = grounded
    skill.content_hash = pack.content_hash()
    skill.grounded_at = datetime.now(timezone.utc).isoformat()
    if not skill.capability and pack.user_request:
        skill.capability = pack.user_request.strip()[:80]


def _is_category_compliance_miss(result: GroundingResult) -> bool:
    """True when the model omitted/misnamed root_cause_category (not a taxonomy gap)."""
    reason = (getattr(result, "reason", None) or "").strip().lower()
    return (
        "missing root_cause_category" in reason
        or "unknown root_cause_category" in reason
    )


def _bug_pattern_category_retry_user(user_content: str) -> str:
    from .bug_concepts import taxonomy_category_ids

    cats = ", ".join(taxonomy_category_ids())
    return (
        user_content
        + "\n\nYou did not return a valid root_cause_category last time. "
        + f"It must be exactly one of: {cats}\n"
        + "Rewrite the full bug_pattern JSON with a valid root_cause_category.\n"
    )


def generate_skill(
    topic: str,
    *,
    model_name: Optional[str] = None,
    context: str = "",
    created_by: Optional[str] = None,
    grounding_pack: Optional[GroundingPack] = None,
    two_phase: bool = True,
    context_budget: int = DEFAULT_CONTEXT_BUDGET,
    prefer_bug_pattern: bool = False,
) -> Tuple[Optional[Skill], Optional[str], Optional[GroundingResult]]:
    """
    Ask the connected model to write a skill for `topic`.

    When `grounding_pack` is provided, uses a two-phase extract→write flow and
    runs a grounding check. Returns (skill, error_message, grounding_result).

    ``prefer_bug_pattern`` selects the bug-pattern JSON schema (symptom → cause →
    fix → verification) instead of a feature playbook/scaffold.
    """
    topic = (topic or "").strip()
    if not topic:
        return None, "Describe what the skill should cover.", None

    try:
        model = resolve_model(model_name)
    except Exception as err:
        return None, f"Could not load model: {err}", None

    pack = grounding_pack
    pack_text = ""
    if pack is not None:
        pack_text = format_grounding_pack(pack)[:context_budget]
    elif context:
        pack_text = context[:context_budget]

    extract = None
    if pack is not None and two_phase and pack_text and not prefer_bug_pattern:
        extract = _extract_phase(model, pack_text, topic)

    if prefer_bug_pattern:
        from .bug_concepts import taxonomy_category_ids

        cats = ", ".join(taxonomy_category_ids())
        user_content = (
            f"Create a bug_pattern skill from this completed fix:\n{topic}\n\n"
            f"Curated root_cause_category ids (pick ONE):\n{cats}\n"
        )
        if pack_text:
            user_content += (
                "\nGROUNDING EVIDENCE — diagnosis MUST match the real diff:\n"
                f"{pack_text}\n"
            )
        system = BUG_PATTERN_SYSTEM
    else:
        user_content = f"Create a skill covering:\n{topic}\n"
        if extract:
            user_content += (
                "\nUse ONLY this extracted fact sheet (do not invent symbols):\n"
                + json.dumps(extract, indent=2)[:8000]
                + "\n"
            )
            # Still attach a short evidence appendix for file paths / snippets
            user_content += "\nEvidence appendix (for citations only):\n"
            user_content += pack_text[: min(12000, context_budget)] + "\n"
        elif pack_text:
            user_content += (
                "\nGROUNDING EVIDENCE — document only what appears below; "
                "do not invent APIs:\n"
                f"{pack_text}\n"
            )
        system = SKILL_SYSTEM

    raw, err = _call_model(model, system, user_content)
    if err:
        return None, err, None

    data = _extract_json(raw)
    skill = _skill_from_data(
        data or {},
        topic,
        created_by=created_by,
        source="generate",
        raw_fallback=raw or "",
    )
    if not skill:
        return None, "Model did not return skill content.", None

    # Prefer capability from extract phase
    if extract and extract.get("capability") and not skill.capability:
        skill.capability = str(extract.get("capability")).strip()[:120]

    if prefer_bug_pattern:
        skill.kind = SKILL_KIND_BUG_PATTERN
    apply_inferred_metadata(skill, source="generate")

    grounding_result: Optional[GroundingResult] = None
    if pack is not None:
        check_text = f"{skill.title}\n{skill.description}\n{skill.content}"
        if skill.kind == SKILL_KIND_BUG_PATTERN:
            from .grounding import check_bug_pattern_grounding

            grounding_result = check_bug_pattern_grounding(skill, pack)
        else:
            grounding_result = check_grounding(check_text, pack)
        _apply_pack_metadata(skill, pack, grounding_result)
        if not grounding_result.ok:
            # One regenerate attempt. Category compliance misses get a targeted
            # taxonomy reminder; other failures keep the symbol-grounding nudge.
            if skill.kind == SKILL_KIND_BUG_PATTERN and _is_category_compliance_miss(
                grounding_result
            ):
                retry_user = _bug_pattern_category_retry_user(user_content)
                retry_system = BUG_PATTERN_SYSTEM
            else:
                missing = ", ".join(grounding_result.missing_symbols[:12])
                retry_user = (
                    user_content
                    + "\n\nPREVIOUS DRAFT FAILED GROUNDING CHECK.\n"
                    + f"These names were NOT in the evidence — remove them: {missing}\n"
                    + "Rewrite the skill using ONLY symbols from the evidence list.\n"
                )
                retry_system = (
                    BUG_PATTERN_SYSTEM if prefer_bug_pattern else SKILL_SYSTEM
                )
            raw2, err2 = _call_model(model, retry_system, retry_user)
            if not err2 and raw2:
                data2 = _extract_json(raw2)
                skill2 = _skill_from_data(
                    data2 or {},
                    topic,
                    created_by=created_by,
                    source="generate",
                    raw_fallback=raw2,
                )
                if skill2:
                    if extract and extract.get("capability") and not skill2.capability:
                        skill2.capability = str(extract.get("capability")).strip()[:120]
                    if prefer_bug_pattern:
                        skill2.kind = SKILL_KIND_BUG_PATTERN
                    apply_inferred_metadata(skill2, source="generate")
                    check_text2 = f"{skill2.title}\n{skill2.description}\n{skill2.content}"
                    if skill2.kind == SKILL_KIND_BUG_PATTERN:
                        from .grounding import check_bug_pattern_grounding

                        result2 = check_bug_pattern_grounding(skill2, pack)
                    else:
                        result2 = check_grounding(check_text2, pack)
                    _apply_pack_metadata(skill2, pack, result2)
                    skill = skill2
                    grounding_result = result2

        skill.needs_review = not grounding_result.ok

    return skill, None, grounding_result
