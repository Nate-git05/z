"""Z uncertainty tree — structured, actionable risk/confidence nodes."""

from .schema import (
    Area,
    NodeStatus,
    NodeType,
    RequirementItem,
    TaskChecklist,
    Tier,
    UncertaintyNode,
)
from .store import UncertaintyStore, sort_nodes
from .engine import UncertaintyEngine, SessionContext, attach_engine_to_coder
from .tree import build_tree, flatten_for_display
from .actions import apply_action
from .checklist import decompose_request, format_checklist_for_user
from .evidence_strategy import (
    ALL_REQUIREMENT_KINDS,
    KIND_VERIFIERS,
    REGISTERED_KINDS,
    STATUS_UNVERIFIABLE,
    allows_fully,
    combine_model_and_mechanical,
    status_from_strategy,
    verifier_for,
)
from .absorption_taxonomy import ABSORPTION_TAXONOMY, scan_failure_absorption
from .sibling_traits import find_sibling_companion_gaps
from .gate import GateResult, prepare_commit, report_auto_fix_exhaustion
from .verify import VerificationRecord, verify_edits
from .auto_act import default_prompt_for_node, plan_auto_act
from .context import assess_repo_maturity

__all__ = [
    "Area",
    "NodeStatus",
    "NodeType",
    "RequirementItem",
    "TaskChecklist",
    "Tier",
    "UncertaintyNode",
    "UncertaintyStore",
    "UncertaintyEngine",
    "SessionContext",
    "attach_engine_to_coder",
    "sort_nodes",
    "build_tree",
    "flatten_for_display",
    "apply_action",
    "decompose_request",
    "format_checklist_for_user",
    "ALL_REQUIREMENT_KINDS",
    "KIND_VERIFIERS",
    "REGISTERED_KINDS",
    "STATUS_UNVERIFIABLE",
    "allows_fully",
    "combine_model_and_mechanical",
    "status_from_strategy",
    "verifier_for",
    "ABSORPTION_TAXONOMY",
    "scan_failure_absorption",
    "find_sibling_companion_gaps",
    "GateResult",
    "prepare_commit",
    "report_auto_fix_exhaustion",
    "VerificationRecord",
    "verify_edits",
    "default_prompt_for_node",
    "plan_auto_act",
    "assess_repo_maturity",
]
