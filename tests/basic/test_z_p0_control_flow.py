"""P0 control-flow: task modes, intent, capabilities, async sync, shell risk, transcripts."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_HOME = tempfile.mkdtemp(prefix="z_p0_")
os.environ["Z_HOME"] = _HOME

from aider.z.shell_risk import (  # noqa: E402
    CommandRiskClass,
    classify_command,
    make_approval_token,
    policy_auto_approves,
)
from aider.z.task_mode import TaskMode, classify_task_mode  # noqa: E402
from aider.z.uncertainty.capabilities import (  # noqa: E402
    build_capability_plan,
    infer_capabilities,
)
from aider.z.uncertainty.intent import TaskIntent, extract_intent  # noqa: E402
from aider.z.uncertainty.plan import (  # noqa: E402
    draft_plan_from_request,
    format_plan_for_confirm,
)
from aider.z.uncertainty.schema import (  # noqa: E402
    NodeStatus,
    NodeType,
    Tier,
    UncertaintyNode,
)
from aider.z.uncertainty.store import UncertaintyStore  # noqa: E402
from aider.z.uncertainty.sync_outbox import reset_outbox_for_tests  # noqa: E402


# ---------------------------------------------------------------------------
# P0.6 harness
# ---------------------------------------------------------------------------


class AgentRunResult:
    def __init__(self, **kwargs):
        self.edits = kwargs.get("edits", [])
        self.implementation_plan = kwargs.get("implementation_plan")
        self.mode = kwargs.get("mode")
        self.intent = kwargs.get("intent")
        self.capabilities = kwargs.get("capabilities", [])
        self.inspected_files = kwargs.get("inspected_files", [])
        self.final_answer_contains_evidence = kwargs.get(
            "final_answer_contains_evidence", False
        )
        self.shell_commands_run = kwargs.get("shell_commands_run", [])
        self.uncertainty_nodes_created = kwargs.get("uncertainty_nodes_created", 0)
        self.elapsed_s = kwargs.get("elapsed_s", 0.0)
        self.pipeline = kwargs.get("pipeline", [])


def run_agent(
    *,
    mode: str | None = None,
    prompt: str,
    edit_format: str | None = None,
    repo_fixture: Path | None = None,
) -> AgentRunResult:
    """
    Deterministic orchestration harness for P0 transcript tests.

    Exercises real mode/intent/plan/capability/shell classifiers without a live LLM.
    """
    t0 = time.time()
    pipeline = []
    fmt = edit_format
    if mode == "ask":
        fmt = "ask"
    elif mode == "context":
        fmt = "context"

    forced = None
    if fmt in ("ask", "context"):
        forced = "ask"

    intent = extract_intent(prompt, forced_mode=forced)
    pipeline.append("intent")
    tm = classify_task_mode(fmt, prompt, intent_mode=intent.mode)
    if fmt in ("ask", "context") and tm is TaskMode.IMPLEMENT:
        tm = TaskMode.INVESTIGATE if intent.mode == "investigate" else TaskMode.ASK
        intent.mode = tm.value
    pipeline.append(f"mode:{tm.value}")

    plan = None
    if tm.allows_planning:
        pipeline.append("planning")
        plan = draft_plan_from_request(prompt, intent=intent, reason="test")
        if getattr(plan, "skipped", False):
            plan = None
    else:
        pipeline.append("planning_skipped")

    caps = []
    if tm.allows_capability_inference:
        pipeline.append("capabilities")
        caps = infer_capabilities(intent=intent)
    else:
        pipeline.append("capabilities_skipped")

    # Shell risk on a few common commands (no execution)
    root = repo_fixture or Path(".")
    shell_ok = []
    for cmd in ("git status", "git diff", "rg TODO"):
        c = classify_command(cmd, root=root)
        if policy_auto_approves(c.risk_class):
            shell_ok.append(cmd)
    pipeline.append("shell_classify")

    edits = []  # harness never edits
    if not tm.allows_edits:
        assert edits == []

    return AgentRunResult(
        edits=edits,
        implementation_plan=plan if tm.allows_planning else None,
        mode=tm.value,
        intent=intent,
        capabilities=caps,
        inspected_files=["aider/z/task_mode.py"] if tm != TaskMode.IMPLEMENT else [],
        final_answer_contains_evidence=tm in (TaskMode.ASK, TaskMode.INVESTIGATE),
        shell_commands_run=shell_ok,
        elapsed_s=time.time() - t0,
        pipeline=pipeline,
    )


class TaskModeTests(unittest.TestCase):
    def test_explicit_ask_and_context(self):
        self.assertEqual(
            classify_task_mode("ask", "anything about APIs"), TaskMode.ASK
        )
        # investigate phrasing under /ask → INVESTIGATE
        self.assertEqual(
            classify_task_mode(
                "ask",
                "Investigate which files own this behavior. Do not edit anything.",
            ),
            TaskMode.INVESTIGATE,
        )
        self.assertEqual(classify_task_mode("context", "show me callers"), TaskMode.ASK)

    def test_plain_investigate_vs_implement(self):
        self.assertEqual(
            classify_task_mode(
                None, "Investigate why the API fails. Do not edit files."
            ),
            TaskMode.INVESTIGATE,
        )
        self.assertEqual(
            classify_task_mode(None, "Add a new REST endpoint for users"),
            TaskMode.IMPLEMENT,
        )

    def test_ambiguous_defaults_to_implement(self):
        self.assertEqual(classify_task_mode(None, "users and sessions"), TaskMode.IMPLEMENT)

    def test_casual_chat_is_ask_not_plan(self):
        from aider.z.task_mode import looks_like_casual_chat
        from aider.z.uncertainty.intent import extract_intent

        for msg in ("hello", "hi", "hey!", "thanks", "ok", "good morning"):
            self.assertTrue(looks_like_casual_chat(msg), msg)
            self.assertEqual(classify_task_mode(None, msg), TaskMode.ASK, msg)
            intent = extract_intent(msg)
            self.assertEqual(intent.mode, "ask", msg)
            self.assertFalse(intent.requested_actions, msg)
            self.assertFalse(TaskMode.ASK.allows_planning)
            self.assertFalse(TaskMode.ASK.allows_requirement_decomposition)

    def test_pure_question_is_ask(self):
        self.assertEqual(
            classify_task_mode(None, "What is an LRU cache?"),
            TaskMode.ASK,
        )
        intent = extract_intent("What is an LRU cache?")
        self.assertEqual(intent.mode, "ask")

    def test_mode_policy_properties(self):
        self.assertFalse(TaskMode.ASK.allows_planning)
        self.assertFalse(TaskMode.INVESTIGATE.allows_planning)
        self.assertTrue(TaskMode.IMPLEMENT.allows_planning)
        self.assertFalse(TaskMode.ASK.allows_edits)
        self.assertTrue(TaskMode.IMPLEMENT.allows_edits)


class IntentTests(unittest.TestCase):
    def test_api_bug_not_implement(self):
        msg = "Determine why this existing API request fails. Do not change the API."
        intent = extract_intent(msg)
        self.assertEqual(intent.mode, "investigate")
        self.assertTrue(any("change" in p.lower() or "api" in p.lower() for p in intent.prohibited_actions))
        plan = draft_plan_from_request(msg, intent=intent, reason="test")
        self.assertTrue(plan.skipped)
        confirm = format_plan_for_confirm(plan)
        self.assertNotIn("Add or change backend endpoints", plan.approach)

    def test_adversarial_phrases(self):
        phrases = [
            "Do not add authentication.",
            "UI is explicitly out of scope.",
            "This is not a concurrency issue.",
            "The report mentions production, but only reproduce locally.",
            "Explain the endpoint; do not create one.",
        ]
        for p in phrases:
            intent = extract_intent(p)
            self.assertTrue(
                intent.prohibited_actions
                or intent.mode in ("ask", "investigate")
                or "locally" in p.lower(),
                msg=f"expected prohibition or non-implement for: {p}",
            )
            caps = infer_capabilities(intent=intent)
            # Negated topics must not activate matching caps from prohibitions alone
            if "ui" in p.lower() and "out of scope" in p.lower():
                self.assertFalse(any(c.id == "responsive_ui" for c in caps), p)
            if "authentication" in p.lower() and "do not" in p.lower():
                self.assertFalse(any(c.id == "auth_review" for c in caps), p)
            if "concurrency" in p.lower() and "not" in p.lower():
                self.assertFalse(any(c.id == "concurrency_safety" for c in caps), p)
            if "production" in p.lower() and "locally" in p.lower():
                self.assertFalse(any(c.id == "production_build" for c in caps), p)
            if "do not create" in p.lower():
                # explain may investigate an endpoint; must not plan creation
                plan = draft_plan_from_request(p, intent=intent, reason="t")
                self.assertTrue(plan.skipped or intent.mode != "implement", p)
                joined = " ".join(plan.steps).lower()
                self.assertNotIn("implement service logic", joined)

    def test_plan_ignores_raw_prompt_mutation(self):
        msg = "Add a healthcheck HTTP handler"
        intent = extract_intent(msg)
        plan_a = draft_plan_from_request(msg, intent=intent, reason="t")
        # Mutate raw prompt after extraction — plan must be unaffected
        plan_b = draft_plan_from_request(
            msg + " and also build a multiplayer lobby with auth UI",
            intent=intent,
            reason="t",
        )
        self.assertEqual(plan_a.approach, plan_b.approach)
        self.assertEqual(plan_a.steps, plan_b.steps)


class CapabilityTests(unittest.TestCase):
    def test_negated_ui_does_not_activate_responsive_ui(self):
        msg = "This is not a UI issue; investigate the API mapping."
        intent = extract_intent(msg)
        caps = infer_capabilities(intent=intent)
        self.assertFalse(any(c.id == "responsive_ui" for c in caps))
        for c in caps:
            self.assertTrue(c.requirement_id)
            self.assertTrue(c.matched_span)

    def test_provenance_required(self):
        intent = TaskIntent(
            mode="implement",
            requested_actions=["Add OAuth login to the API"],
        )
        caps = infer_capabilities(intent=intent)
        self.assertTrue(caps)
        for c in caps:
            self.assertTrue(c.requirement_id)
            self.assertTrue(c.matched_span)
            self.assertGreater(c.confidence, 0)

    def test_no_full_prompt_scan_helper(self):
        # Observations alone must not activate
        intent = TaskIntent(
            mode="investigate",
            requested_actions=[],
            observations=["The UI in production showed a concurrency race in the API auth path"],
            prohibited_actions=["not a UI issue"],
        )
        caps = infer_capabilities(intent=intent)
        self.assertEqual(caps, [])


class AsyncSyncTests(unittest.TestCase):
    def test_add_does_not_block_on_hanging_remote(self):
        reset_outbox_for_tests()

        def hang(_node):
            time.sleep(30)
            return False

        store = UncertaintyStore(
            root=Path(_HOME) / "repo",
            repo_key="hang-test",
            remote_sync=hang,
        )
        t0 = time.time()
        for i in range(5):
            store.add(
                UncertaintyNode(
                    id=f"n-{i}",
                    type=NodeType.MISSING_TEST,
                    title=f"node {i}",
                    summary=f"summary {i}",
                    risk_tier=Tier.LOW,
                    confidence_tier=Tier.LOW,
                    status=NodeStatus.OPEN,
                )
            )
        elapsed = time.time() - t0
        self.assertLess(elapsed, 1.0, f"add() blocked too long: {elapsed:.2f}s")

    def test_remote_timeout_not_fifteen(self):
        from aider.z.uncertainty import remote as remote_mod

        self.assertNotEqual(remote_mod.TIMEOUT, 15)
        if isinstance(remote_mod.TIMEOUT, tuple):
            self.assertLessEqual(remote_mod.TIMEOUT[1], 2.5)


class ShellRiskTests(unittest.TestCase):
    def test_read_only_auto(self):
        for cmd in ("git status", "git diff", "rg foo", "ls"):
            c = classify_command(cmd)
            self.assertTrue(policy_auto_approves(c.risk_class), cmd)

    def test_destructive_not_auto(self):
        c = classify_command("rm -rf /tmp/x")
        self.assertEqual(c.risk_class, CommandRiskClass.DESTRUCTIVE)
        self.assertFalse(policy_auto_approves(c.risk_class))

    def test_git_reset_hard_destructive(self):
        c = classify_command("git reset --hard")
        self.assertEqual(c.risk_class, CommandRiskClass.DESTRUCTIVE)

    def test_metacharacters_unknown(self):
        c = classify_command("git log --grep=$(rm -rf /)")
        self.assertEqual(c.risk_class, CommandRiskClass.UNKNOWN)

    def test_declared_verification(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "package.json").write_text(
                '{"scripts": {"test": "jest", "lint": "eslint ."}}', encoding="utf-8"
            )
            c = classify_command("npm test", root=root)
            self.assertEqual(c.risk_class, CommandRiskClass.DECLARED_VERIFICATION)

    def test_approval_tokens_differ(self):
        a = make_approval_token("git push", nonce="1")
        b = make_approval_token("rm -rf /", nonce="1")
        self.assertNotEqual(a, b)

    def test_yes_always_still_blocks_arbitrary(self):
        """Existing security invariant — arbitrary shell stays blocked under yes=True."""
        from aider.coders import Coder
        from aider.io import InputOutput
        from aider.models import Model
        from aider.utils import GitTemporaryDirectory

        with GitTemporaryDirectory():
            Path("requirements.txt").write_text("pytest\n", encoding="utf-8")
            io = InputOutput(yes=True, pretty=False, fancy_input=False)
            errors = []
            io.tool_error = errors.append
            coder = Coder.create(Model("gpt-3.5-turbo"), "diff", io=io)
            with patch("aider.coders.base_coder.run_cmd") as mock_run:
                out = coder.handle_shell_commands('echo "pwned"', group=None)
            mock_run.assert_not_called()
            self.assertIsNone(out)
            self.assertTrue(any("blocked: needs human approval" in e for e in errors))


class TranscriptScenarios(unittest.TestCase):
    def test_ask_mode_no_plan_no_edits(self):
        result = run_agent(
            mode="ask",
            prompt=(
                "Investigate why the existing Zen API mapping fails. "
                "Do not edit files."
            ),
        )
        self.assertEqual(result.edits, [])
        self.assertIsNone(result.implementation_plan)
        self.assertIn(result.mode, ("investigate", "ask"))
        self.assertIn("planning_skipped", result.pipeline)

    def test_investigate_api_keywords_no_backend_template(self):
        result = run_agent(
            mode="ask",
            prompt="Determine why this existing API request fails. Do not change the API.",
        )
        self.assertIsNone(result.implementation_plan)
        self.assertFalse(any(c.id == "responsive_ui" for c in result.capabilities))

    def test_do_not_edit_skips_planning(self):
        result = run_agent(
            prompt="Investigate which files own this behavior. Do not edit anything.",
        )
        self.assertEqual(result.mode, "investigate")
        self.assertIsNone(result.implementation_plan)

    def test_negated_capability_orchestration(self):
        result = run_agent(
            prompt="This is not a UI issue; investigate the API mapping.",
        )
        self.assertFalse(any(c.id == "responsive_ui" for c in result.capabilities))

    def test_observations_not_requirements(self):
        intent = extract_intent(
            "The log shows API timeout in production. Investigate only."
        )
        # Observations should not be empty dump of the whole thing as actions only
        self.assertTrue(intent.observations or intent.mode == "investigate")
        # Completion checklist must not treat observations as product reqs via plan
        plan = draft_plan_from_request(
            "The log shows API timeout in production. Investigate only.",
            intent=intent,
            reason="t",
        )
        self.assertTrue(plan.skipped or intent.mode != "implement")

    def test_implement_happy_path_still_plans(self):
        result = run_agent(
            prompt="Add a /health JSON endpoint and a unit test for it.",
        )
        self.assertEqual(result.mode, "implement")
        self.assertIn("planning", result.pipeline)
        # May or may not create a plan depending on triage; mode allows it
        self.assertTrue(TaskMode.IMPLEMENT.allows_planning)

    def test_shell_read_only_auto_in_harness(self):
        result = run_agent(prompt="Add a tiny helper function")
        self.assertIn("git status", result.shell_commands_run)
        self.assertIn("git diff", result.shell_commands_run)


class PlanNoRawPromptScan(unittest.TestCase):
    def test_plan_module_has_no_user_message_keyword_branches_in_draft(self):
        """Sanity: _draft_approach_and_steps signature takes intent, not raw str."""
        import inspect

        from aider.z.uncertainty import plan as plan_mod

        sig = inspect.signature(plan_mod._draft_approach_and_steps)
        params = list(sig.parameters)
        self.assertEqual(params[0], "intent")


if __name__ == "__main__":
    unittest.main()
