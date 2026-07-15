"""Uncertainty engine — runs detectors after edits / tests / MCP use."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Set

from .checklist import (
    TaskChecklist,
    bind_evidence,
    checklist_gap_details,
    decompose_request,
    rescore_checklist_with_evidence,
    rescore_checklist_with_model,
)
from .context import (
    assess_repo_maturity,
    filter_scaffold_files,
    prioritize_nodes,
    should_emit_new_file_noise,
    should_emit_pattern_misfit,
)
from .detectors import (
    PatternSearchResult,
    count_symbol_references,
    detect_api_assumptions,
    detect_blast_radius,
    detect_edge_cases,
    detect_failure_blind_spots,
    detect_fragile_logic,
    detect_high_confidence,
    detect_high_stakes_and_migration,
    detect_missing_or_failing_tests,
    detect_pattern_issues,
    detect_requirement_gaps,
    detect_todo_comments,
    detect_unverifiable_config,
    extract_config_refs,
    find_relevant_tests,
    scan_todo_markers,
)
from .risk import collect_base_signals
from .schema import UncertaintyNode
from .store import UncertaintyStore


@dataclass
class SessionContext:
    """Mutable session state for uncertainty detection."""

    root: Path
    store: UncertaintyStore
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    user_label: Optional[str] = None
    live_verified_apis: Set[str] = field(default_factory=set)
    assumed_apis: Set[str] = field(default_factory=set)
    mcp_unverifiable: Set[str] = field(default_factory=set)
    checklist: Optional[TaskChecklist] = None
    current_task_id: Optional[str] = None
    current_task_title: Optional[str] = None
    edge_cases_from_model: List[str] = field(default_factory=list)
    # Last agent reply — used to see if structural branches were discussed
    discussed_text: str = ""
    # Optional unified diff for scoping structural edge detection to changed lines
    last_diff: str = ""
    # Accumulated session execution facts for process-requirement evidence
    execution_log: str = ""
    user_decisions: List[str] = field(default_factory=list)
    last_verification: Optional[object] = None
    migration_data_impact: Optional[str] = None
    new_files_this_turn: List[str] = field(default_factory=list)
    pattern_results: dict = field(default_factory=dict)
    # Optional callable(prompt: str) -> str for structured checklist rescore
    model_complete: Optional[object] = None


class UncertaintyEngine:
    def __init__(self, ctx: SessionContext):
        self.ctx = ctx

    @property
    def store(self) -> UncertaintyStore:
        return self.ctx.store

    def begin_task(self, user_message: str, title: Optional[str] = None) -> TaskChecklist:
        checklist = decompose_request(title or "", user_message)
        self.ctx.checklist = checklist
        self.ctx.current_task_id = checklist.task_id
        self.ctx.current_task_title = checklist.title
        return checklist

    def record_live_api(self, api_name: str) -> None:
        if api_name:
            self.ctx.live_verified_apis.add(api_name)

    def record_assumed_api(self, api_name: str) -> None:
        if api_name:
            self.ctx.assumed_apis.add(api_name)

    def record_mcp_unverifiable(self, tool_name: str) -> None:
        if tool_name:
            self.ctx.mcp_unverifiable.add(tool_name)

    def record_edge_cases(self, cases: Sequence[str]) -> None:
        self.ctx.edge_cases_from_model = [c.strip() for c in cases if c and c.strip()]

    def record_discussed_text(self, text: str) -> None:
        self.ctx.discussed_text = text or ""
        if text:
            self.ctx.execution_log = (
                (self.ctx.execution_log or "") + "\n" + text
            )[-20000:]

    def record_diff(self, diff: str) -> None:
        self.ctx.last_diff = diff or ""

    def record_execution(self, note: str) -> None:
        if note:
            self.ctx.execution_log = (
                (self.ctx.execution_log or "") + "\n" + note
            )[-20000:]

    def record_user_decision(self, note: str) -> None:
        if note:
            self.ctx.user_decisions.append(note)

    def record_migration_impact(self, text: str) -> None:
        self.ctx.migration_data_impact = text

    def analyze_edits(
        self,
        files_changed: Sequence[str],
        *,
        symbols: Sequence[str] = (),
        tests_passed: Optional[bool] = None,
        file_contents: Optional[dict[str, str]] = None,
        run_gap_analysis: bool = True,
        diff: Optional[str] = None,
        discussed_text: Optional[str] = None,
    ) -> List[UncertaintyNode]:
        """Run all concrete detectors for a batch of edited files."""
        root = self.ctx.root
        files = [self._rel(f) for f in files_changed]
        symbols = list(symbols)
        contents = file_contents or self._read_files(files)

        # Detect new files
        new_files = []
        for f in files:
            abs_path = root / f
            # Heuristic: if git isn't available, treat empty prior pattern search as new
            if f in self.ctx.new_files_this_turn or not abs_path.exists():
                new_files.append(f)
            elif f not in self.ctx.pattern_results:
                # Search for similar files by stem / suffix
                self.ctx.pattern_results[f] = self._search_patterns(f)
                if not self.ctx.pattern_results[f].matches and self._looks_new(f):
                    new_files.append(f)

        for f in files:
            if f not in self.ctx.pattern_results:
                self.ctx.pattern_results[f] = self._search_patterns(f)

        # Assumed APIs from imports in changed files
        for text in contents.values():
            for api in self._guess_external_apis(text):
                if api not in self.ctx.live_verified_apis:
                    self.ctx.assumed_apis.add(api)

        signals = collect_base_signals(
            files,
            symbols,
            blast_radius_threshold=int(os.environ.get("Z_BLAST_RADIUS_THRESHOLD", "5") or "5"),
        )

        # Pattern match quality for high confidence
        any_match = any(
            (self.ctx.pattern_results.get(f) or PatternSearchResult()).matches for f in files
        )
        signals.pattern_match_found = any_match if files else None
        signals.closely_matches_tested_pattern = bool(
            any_match
            and not any(
                (self.ctx.pattern_results.get(f) or PatternSearchResult()).conflicting for f in files
            )
        )

        meta = dict(
            task_id=self.ctx.current_task_id,
            task_title=self.ctx.current_task_title,
            created_by_session=self.ctx.session_id,
            created_by_user=self.ctx.user_label,
        )

        maturity = assess_repo_maturity(root)
        emit_new_file = should_emit_new_file_noise(maturity)
        emit_misfit = should_emit_pattern_misfit(maturity)
        # Soften blast radius in greenfield (few peers → noisy)
        if maturity == "greenfield":
            signals.blast_radius_threshold = max(signals.blast_radius_threshold, 20)

        nodes: List[UncertaintyNode] = []

        # Tests → Untested Path
        relevant = find_relevant_tests(root, files, symbols)
        nodes.extend(
            detect_missing_or_failing_tests(
                signals,
                relevant_tests=relevant,
                tests_passed=tests_passed,
                **meta,
            )
        )

        # High stakes / migration
        nodes.extend(
            detect_high_stakes_and_migration(
                signals,
                file_contents=contents,
                migration_data_impact=self.ctx.migration_data_impact,
                **meta,
            )
        )

        # API / MCP → Unverified Assumption
        nodes.extend(
            detect_api_assumptions(
                signals,
                assumed_apis=sorted(self.ctx.assumed_apis),
                live_verified_apis=self.ctx.live_verified_apis,
                mcp_unverifiable=sorted(self.ctx.mcp_unverifiable),
                **meta,
            )
        )

        # Patterns / new files (context-aware noise)
        candidate_new = new_files or [
            f
            for f in files
            if not (self.ctx.pattern_results.get(f) or PatternSearchResult()).matches
            and self._looks_new(f)
        ]
        candidate_new = filter_scaffold_files(candidate_new)
        nodes.extend(
            detect_pattern_issues(
                signals,
                new_files=candidate_new,
                pattern_results=self.ctx.pattern_results,
                emit_new_file_noise=emit_new_file,
                emit_pattern_misfit=emit_misfit,
                **meta,
            )
        )
        for f in files:
            pr = self.ctx.pattern_results.get(f)
            if pr and pr.conflicting and f not in candidate_new:
                nodes.extend(
                    detect_pattern_issues(
                        signals,
                        new_files=[f],
                        pattern_results={f: pr},
                        emit_new_file_noise=False,
                        emit_pattern_misfit=True,
                        **meta,
                    )
                )

        # Integration ripple (blast radius)
        if maturity != "greenfield":
            for sym in symbols or self._extract_symbols(contents):
                count, refs = count_symbol_references(root, sym, exclude_files=files)
                nodes.extend(
                    detect_blast_radius(
                        signals,
                        reference_count=count,
                        referenced_symbol=sym,
                        referencing_files=refs,
                        **meta,
                    )
                )

        # TODOs
        todos_by_file = {}
        for f, text in contents.items():
            hits = scan_todo_markers(text)
            if hits:
                todos_by_file[f] = hits
        nodes.extend(detect_todo_comments(signals, todos_by_file=todos_by_file, **meta))

        # Unverifiable config
        config_refs = {f: extract_config_refs(text) for f, text in contents.items()}
        config_refs = {f: refs for f, refs in config_refs.items() if refs}
        nodes.extend(
            detect_unverifiable_config(signals, config_refs_by_file=config_refs, **meta)
        )

        # Fragile logic + failure blind spots
        nodes.extend(detect_fragile_logic(signals, file_contents=contents, **meta))
        nodes.extend(detect_failure_blind_spots(signals, file_contents=contents, **meta))

        # Edge case blind spots — structural AST/regex first; model list supplements
        test_blob = ""
        try:
            for tpath in relevant or []:
                tp = root / tpath
                if tp.is_file():
                    test_blob += tp.read_text(encoding="utf-8", errors="ignore")[:8000]
                    test_blob += "\n"
        except OSError:
            pass
        nodes.extend(
            detect_edge_cases(
                signals,
                edge_cases=self.ctx.edge_cases_from_model,
                file_contents=contents,
                discussed_text=(
                    discussed_text
                    if discussed_text is not None
                    else self.ctx.discussed_text
                ),
                test_blob=test_blob,
                diff=diff if diff is not None else self.ctx.last_diff,
                **meta,
            )
        )

        # Requirement gaps — evidence-bound structured rescore
        if run_gap_analysis and self.ctx.checklist:
            evidence = bind_evidence(
                self.ctx.checklist,
                files_changed=files,
                file_contents=contents,
                symbols=symbols,
                test_files=relevant,
                execution_log=self.ctx.execution_log or self.ctx.discussed_text,
                user_decisions=self.ctx.user_decisions,
                verification=self.ctx.last_verification,
            )
            model_complete = getattr(self.ctx, "model_complete", None)
            if callable(model_complete):
                rescore_checklist_with_model(
                    self.ctx.checklist, evidence, model_complete=model_complete
                )
            else:
                rescore_checklist_with_evidence(self.ctx.checklist, evidence)
            gaps = checklist_gap_details(self.ctx.checklist, evidence)
            nodes.extend(
                detect_requirement_gaps(
                    signals,
                    checklist=self.ctx.checklist,
                    gap_details=gaps,
                    **meta,
                )
            )

        # Evidence of Safety (positive)
        nodes.extend(detect_high_confidence(signals, **meta))

        # Cap noise: keep top actionable nodes this turn
        deduped = self._dedupe(nodes)
        deduped = prioritize_nodes(deduped, limit=8)
        self.store.add_many(deduped)
        return deduped

    def _rel(self, path: str) -> str:
        p = Path(path)
        try:
            if p.is_absolute():
                return p.relative_to(self.ctx.root).as_posix()
        except ValueError:
            pass
        return path.replace("\\", "/")

    def _read_files(self, files: Sequence[str]) -> dict[str, str]:
        out = {}
        for f in files:
            path = self.ctx.root / f
            if path.is_file():
                try:
                    out[f] = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    pass
        return out

    def _looks_new(self, rel: str) -> bool:
        # Without git history, treat very short / empty sibling matches as new
        pr = self.ctx.pattern_results.get(rel)
        return not pr or not pr.matches

    def _search_patterns(self, rel: str) -> PatternSearchResult:
        path = Path(rel)
        stem = path.stem
        suffix = path.suffix
        matches: List[str] = []
        for candidate in self.ctx.root.rglob(f"*{suffix}" if suffix else "*"):
            if not candidate.is_file():
                continue
            if any(p in ("node_modules", ".git", "venv", "__pycache__") for p in candidate.parts):
                continue
            try:
                crel = candidate.relative_to(self.ctx.root).as_posix()
            except ValueError:
                continue
            if crel == rel:
                continue
            if stem and stem.lower() in candidate.stem.lower():
                matches.append(crel)
            elif suffix and candidate.parent.name == path.parent.name and candidate.stem != stem:
                # same directory, same type — weak pattern
                if len(matches) < 5:
                    matches.append(crel)
        # Conflicting if many different parent dirs
        parents = {Path(m).parent.as_posix() for m in matches}
        conflicting = len(parents) > 2 and len(matches) > 2
        return PatternSearchResult(
            matches=matches[:15],
            conflicting=conflicting,
            searched_for=stem or rel,
        )

    def _guess_external_apis(self, text: str) -> List[str]:
        apis = []
        # Common SDK / HTTP client hints
        patterns = [
            (r"stripe\.", "stripe"),
            (r"paypal", "paypal"),
            (r"openai\.", "openai"),
            (r"anthropic\.", "anthropic"),
            (r"boto3", "aws"),
            (r"google\.cloud", "google-cloud"),
            (r"twilio", "twilio"),
            (r"requests\.(get|post|put|patch|delete)", "http-requests"),
            (r"httpx\.", "httpx"),
            (r"fetch\(", "fetch"),
        ]
        import re

        for pat, name in patterns:
            if re.search(pat, text):
                apis.append(name)
        return apis

    def _extract_symbols(self, contents: dict[str, str]) -> List[str]:
        import re

        symbols = []
        for text in contents.values():
            for m in re.finditer(
                r"^\s*(?:def|class|async def|function|export (?:async )?function|export class)\s+(\w+)",
                text,
                re.M,
            ):
                symbols.append(m.group(1))
        return symbols[:10]

    def _dedupe(self, nodes: List[UncertaintyNode]) -> List[UncertaintyNode]:
        seen = set()
        out = []
        for n in nodes:
            key = (n.type.value, n.title, tuple(n.files_affected[:3]))
            # Also skip if identical open node already in store
            if key in seen:
                continue
            exists = any(
                e.type == n.type
                and e.title == n.title
                and e.status.value not in ("Resolved", "Ignored")
                for e in self.store.nodes.values()
            )
            if exists:
                continue
            seen.add(key)
            out.append(n)
        return out


def attach_engine_to_coder(coder, *, user_label: Optional[str] = None) -> UncertaintyEngine:
    """Create and attach an UncertaintyEngine on a Coder instance."""
    root = Path(getattr(coder, "root", None) or os.getcwd())
    session_id = getattr(coder, "uncertainty_session_id", None) or str(uuid.uuid4())
    coder.uncertainty_session_id = session_id

    def _remote_sync(node: UncertaintyNode):
        try:
            from .remote import sync_node

            sync_node(node, repo_key=str(root))
        except Exception:
            pass

    store = UncertaintyStore(
        root=root,
        repo_key=str(root),
        created_by_session=session_id,
        created_by_user=user_label,
        remote_sync=_remote_sync,
    )
    # Pull workspace history if signed in
    try:
        from .remote import fetch_workspace_nodes

        remote = fetch_workspace_nodes(repo_key=str(root))
        if remote:
            store.merge_remote(remote)
    except Exception:
        pass

    ctx = SessionContext(
        root=root,
        store=store,
        session_id=session_id,
        user_label=user_label,
    )
    engine = UncertaintyEngine(ctx)
    coder.uncertainty_engine = engine
    coder.uncertainty_store = store
    return engine
