"""Control-plane noise budget — compact injects for the coding turn.

Full plans still render in scrollback (``format_plan_for_user``) and stay on
``engine.ctx.plan`` for detectors. Only ``cur_messages`` payloads get thinner.
"""

from __future__ import annotations

import os
from typing import Any, List, Optional, Sequence


def control_plane_compact_enabled() -> bool:
    """Master switch. Default ON. Set Z_CONTROL_PLANE_COMPACT=0 to disable."""
    raw = os.environ.get("Z_CONTROL_PLANE_COMPACT", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def plan_context_full_enabled() -> bool:
    """Escape: restore legacy full plan dump into cur_messages."""
    return os.environ.get("Z_PLAN_CONTEXT_FULL", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def plan_exit_budget() -> int:
    raw = os.environ.get("Z_PLAN_EXIT_CHARS", "").strip()
    if raw.isdigit():
        return max(800, int(raw))
    return 2500


def _cap(items: Sequence[Any], n: int) -> List[Any]:
    return list(items)[:n]


def format_capability_directive(plan) -> str:
    """
    Thin capability block for coding inject — IDs, coverage, gap tips only.
    """
    required = list(getattr(plan, "required", None) or [])
    gaps = list(getattr(plan, "coverage_gaps", None) or [])
    covered = list(getattr(plan, "available_from_skills", None) or [])
    tips = list(getattr(plan, "compensation", None) or [])

    if not required:
        return ""

    lines: List[str] = [
        "# Capability directive",
        "Prove these capabilities; do not treat a missing skill as skippable:",
        "",
    ]
    gap_ids = {getattr(g, "id", None) for g in gaps}
    for c in required:
        cid = getattr(c, "id", "") or ""
        label = getattr(c, "label", "") or cid
        et = getattr(c, "evidence_type", "") or ""
        mark = "gap" if cid in gap_ids else "ok"
        crit = " critical" if getattr(c, "critical", False) else ""
        lines.append(f"- [{mark}] {label} ({et}){crit}")
    if covered:
        lines.append("")
        lines.append("Covered by skills: " + ", ".join(str(s) for s in covered[:8]))
    if tips:
        lines.append("")
        lines.append("Gap compensation:")
        for tip in tips[:6]:
            lines.append(f"- {tip}")
    return "\n".join(lines).rstrip() + "\n"


def capability_plan_fingerprint(plan) -> str:
    """Stable key so identical gap sets are not re-injected."""
    if plan is None:
        return ""
    req = sorted(
        getattr(c, "id", "") or "" for c in (getattr(plan, "required", None) or [])
    )
    gaps = sorted(
        getattr(c, "id", "") or "" for c in (getattr(plan, "coverage_gaps", None) or [])
    )
    return "req:" + ",".join(req) + "|gap:" + ",".join(gaps)


def format_plan_directive(plan) -> str:
    """
    Compact binding plan for cur_messages.

    Detectors still use the full ``PlanningArtifact`` on ctx — this is only
    what the coder model sees in-chat.
    """
    lines: List[str] = [
        "# Approved plan directive (binding)",
        f"Task: {getattr(plan, 'title', '') or '(untitled)'}",
    ]
    reason = (getattr(plan, "reason", None) or "").strip()
    if reason:
        lines.append(f"Why gated: {reason}")
    lines.append("")
    lines.append(
        "Follow this directive. The full plan was shown in scrollback; "
        "detectors still enforce the complete artifact."
    )
    lines.append("")

    approach = (getattr(plan, "approach", None) or "").strip()
    if approach:
        if len(approach) > 400:
            approach = approach[:400].rstrip() + "…"
        lines.append("## Approach")
        lines.append(approach)
        lines.append("")

    steps = list(getattr(plan, "steps", None) or [])
    if steps:
        lines.append("## Steps")
        for i, step in enumerate(_cap(steps, 10), 1):
            lines.append(f"{i}. {step}")
        if len(steps) > 10:
            lines.append(f"… ({len(steps) - 10} more steps in full plan)")
        lines.append("")

    oos = list(getattr(plan, "out_of_scope", None) or [])
    if oos:
        lines.append("## Out of scope")
        for item in _cap(oos, 6):
            lines.append(f"- {item}")
        lines.append("")

    contracts = list(getattr(plan, "validation_contracts", None) or [])
    lines.append("## Validation contracts")
    if contracts:
        for c in contracts:
            lines.append(
                f"- `{getattr(c, 'input_name', '')}`: "
                f"{getattr(c, 'domain', '')} / {getattr(c, 'on_invalid', '')}"
            )
    else:
        lines.append("- (none)")
    lines.append("")

    table = list(getattr(plan, "input_domain_table", None) or [])
    if table:
        lines.append("## Input domains")
        for name, domain, notes in _cap(table, 8):
            lines.append(f"- {name}: {domain} ({notes})")
        lines.append("")

    invs = list(getattr(plan, "invariants", None) or [])
    if invs:
        lines.append("## Invariants")
        for inv in invs:
            lines.append(f"- {inv}")
        lines.append("")

    ambs = list(getattr(plan, "ambiguities", None) or [])
    if ambs:
        lines.append("## Ambiguity resolutions")
        for a in _cap(ambs, 8):
            lines.append(
                f"- {getattr(a, 'ambiguity', '')} → {getattr(a, 'resolution', '')}"
            )
        lines.append("")

    est = list(getattr(plan, "established_solutions", None) or [])
    if est:
        lines.append("## Established solutions (binding)")
        for e in est:
            prefer = getattr(e, "standard_approach", None) or "(name the standard)"
            lines.append(
                f"- [{getattr(e, 'category_id', '')}] "
                f"{getattr(e, 'problem_category', '')}: prefer `{prefer}` "
                f"(decision={getattr(e, 'decision', None) or 'unspecified'})"
            )
            just = getattr(e, "custom_justification", None)
            if just:
                lines.append(f"  custom justification: {just}")
        lines.append("")

    cap = getattr(plan, "capability_plan", None)
    if cap and getattr(cap, "required", None):
        gaps = list(getattr(cap, "coverage_gaps", None) or [])
        n_req = len(cap.required)
        n_gap = len(gaps)
        lines.append(
            f"## Capabilities: {n_req} required, {n_gap} gap(s) "
            "(compensate — do not skip verification)"
        )
        lines.append("")

    arch = getattr(plan, "architecture", None)
    if arch:
        blocking = list(getattr(arch, "blocking_assumptions", None) or [])
        layers = list(getattr(arch, "recommended_layers", None) or [])
        bits = []
        if layers:
            bits.append(" → ".join(str(x) for x in layers[:6]))
        if blocking:
            bits.append(f"{len(blocking)} blocking unknown(s)")
        if bits:
            lines.append("## Architecture: " + "; ".join(bits))
            for b in _cap(blocking, 4):
                lines.append(f"- {b}")
            lines.append("")

    journeys = getattr(plan, "journeys", None)
    jlist = list(getattr(journeys, "journeys", None) or []) if journeys else []
    if jlist:
        lines.append("## Critical journeys")
        for j in _cap(jlist, 5):
            lines.append(
                f"- {getattr(j, 'title', '')} "
                f"(evidence={getattr(j, 'required_evidence_type', '')}, "
                f"risk={getattr(j, 'risk', '')})"
            )
        lines.append("")

    ux = getattr(plan, "ux_model", None)
    if ux:
        lines.append("## UX model: present (see scrollback for states)")
        lines.append("")

    tt = getattr(plan, "transition_table", None)
    if tt:
        lines.append("## Transition table: present (see scrollback)")
        lines.append("")

    ms = getattr(plan, "multi_session_plan", None)
    if ms:
        lines.append("## Multi-session plan: present (see scrollback)")
        lines.append("")

    lines.append(
        "Implement only what this plan authorizes. "
        "Never weaken verification to go green."
    )
    return "\n".join(lines).rstrip() + "\n"


def truncate_plan_exit(text: str, *, budget: Optional[int] = None) -> str:
    """Head+tail truncate for freeform /plan-exit artifacts."""
    lim = budget if budget is not None else plan_exit_budget()
    body = (text or "").strip()
    if len(body) <= lim:
        return body
    head = max(400, int(lim * 0.7))
    tail = max(200, lim - head - 80)
    return (
        body[:head].rstrip()
        + "\n\n… [plan truncated for coding context — full artifact on disk] …\n\n"
        + body[-tail:].lstrip()
    )
