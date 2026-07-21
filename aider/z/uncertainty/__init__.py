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
from .established_solutions import (
    ESTABLISHED_SOLUTIONS,
    scan_invention_in_diff,
)
from .concurrency_checks import (
    analyze_concurrency_change,
    classify_race_outcome,
    discover_race_tools,
    parse_race_count,
    tag_concurrency_relevant,
)
from .dynamic_analysis import (
    DYNAMIC_RISK_CATEGORIES,
    DynamicComparison,
    DynamicRiskCategory,
    DynamicRiskTag,
    SanitizerRunResult,
    SanitizerTool,
    analyze_category,
    analyze_dynamic_risks,
    classify_outcome,
    discover_tools_for_category,
    nodes_from_comparison,
    parse_issue_count,
    tag_category,
    tag_dynamic_risks,
    taxonomy_category_ids,
    worst_blocking_comparison,
)
from .package_checks import discover_package_checks, find_nearest_package_json
from .type_members import check_local_type_members
from .detectors import classify_relevant_tests, find_relevant_tests
from .verify import build_relevant_test_command
from .gate import (
    GateResult,
    prepare_commit,
    report_auto_fix_exhaustion,
    resolve_commit_edit_set,
)
from .verify import VerificationRecord, verify_edits
from .auto_act import default_prompt_for_node, plan_auto_act
from .context import assess_repo_maturity
from .capabilities import build_capability_plan, infer_capabilities
from .architecture import draft_architecture_checkpoint
from .journeys import infer_critical_journeys, mark_journey_evidence
from .completion import evaluate_completion
from .integrity import scan_verification_integrity
from .failure_classify import classify_failure

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
    "ESTABLISHED_SOLUTIONS",
    "scan_invention_in_diff",
    "analyze_concurrency_change",
    "classify_race_outcome",
    "discover_race_tools",
    "parse_race_count",
    "tag_concurrency_relevant",
    "DYNAMIC_RISK_CATEGORIES",
    "DynamicComparison",
    "DynamicRiskCategory",
    "DynamicRiskTag",
    "SanitizerRunResult",
    "SanitizerTool",
    "analyze_category",
    "analyze_dynamic_risks",
    "classify_outcome",
    "discover_tools_for_category",
    "nodes_from_comparison",
    "parse_issue_count",
    "tag_category",
    "tag_dynamic_risks",
    "taxonomy_category_ids",
    "worst_blocking_comparison",
    "discover_package_checks",
    "find_nearest_package_json",
    "check_local_type_members",
    "classify_relevant_tests",
    "find_relevant_tests",
    "build_relevant_test_command",
    "GateResult",
    "prepare_commit",
    "report_auto_fix_exhaustion",
    "resolve_commit_edit_set",
    "VerificationRecord",
    "verify_edits",
    "default_prompt_for_node",
    "plan_auto_act",
    "assess_repo_maturity",
    "build_capability_plan",
    "infer_capabilities",
    "draft_architecture_checkpoint",
    "infer_critical_journeys",
    "mark_journey_evidence",
    "evaluate_completion",
    "scan_verification_integrity",
    "classify_failure",
]
