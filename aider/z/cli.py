"""Top-level `z` CLI — login/auth/models and passthrough to the coding agent."""

from __future__ import annotations

import argparse
import os
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="z",
        description="Z coding agent — account auth and AI pair programming",
    )
    sub = parser.add_subparsers(dest="command")

    login = sub.add_parser(
        "login",
        help="Re-authenticate (normally just run `z` — sign-in happens if needed)",
    )
    login.add_argument(
        "--provider",
        choices=["email", "phone", "google"],
        default=None,
        help=argparse.SUPPRESS,
    )

    auth = sub.add_parser(
        "auth",
        help="Re-authenticate or re-choose BYOK vs Z router (`z auth switch`)",
    )
    auth_sub = auth.add_subparsers(dest="auth_command")
    auth_sub.add_parser(
        "switch",
        help="Re-choose between bring-your-own key and Z's model router",
    )
    reset = sub.add_parser(
        "reset",
        help="Clear saved BYOK/router/model choices and pick again (keeps login)",
    )
    reset.add_argument(
        "--logout",
        action="store_true",
        help="Also sign out (clear ~/.z/credentials)",
    )
    sub.add_parser("logout", help="Sign out and clear ~/.z/credentials")
    sub.add_parser("whoami", help="Show the current Z account / workspace")

    workspace = sub.add_parser("workspace", help="Create/manage a Z workspace")
    workspace_sub = workspace.add_subparsers(dest="workspace_command")

    create_ws = workspace_sub.add_parser("create", help="Create a new workspace")
    create_ws.add_argument("name", nargs="*", help="Workspace name (prompted if omitted)")
    create_ws.add_argument("--organization", default=None, help="Organization name")

    invite = workspace_sub.add_parser("invite", help="Invite a member by email or phone")
    invite.add_argument(
        "identifier",
        nargs="*",
        help="Email or phone number (prompted if omitted)",
    )

    workspace_sub.add_parser("members", help="List current workspace members")
    workspace_sub.add_parser(
        "switch",
        help="Switch active workspace (if in more than one)",
    )

    models = sub.add_parser(
        "models",
        help="List curated current models (and search all known models)",
    )
    models.add_argument(
        "search",
        nargs="?",
        default="",
        help="Optional search string (same as aider --models)",
    )
    models.add_argument(
        "--all",
        action="store_true",
        help="Search the full litellm model catalog instead of the curated list",
    )

    mcp = sub.add_parser("mcp", help="MCP tool connections (managed in the web app)")
    mcp_sub = mcp.add_subparsers(dest="mcp_command")
    mcp_sub.add_parser("list", help="List MCP tools connected to your Z account/workspace")

    unc = sub.add_parser("uncertainty", help="Uncertainty tree utilities")
    unc_sub = unc.add_subparsers(dest="uncertainty_command")
    unc_sub.add_parser(
        "stats",
        help="Show per-detector disposition rates (ignored / force-commit / resolved)",
    )

    bench = sub.add_parser(
        "benchmark",
        help="P2 software-engineering behavior benchmark (run/score/list)",
    )
    bench_sub = bench.add_subparsers(dest="bench_command")
    bench_run = bench_sub.add_parser("run", help="Run the P2 benchmark suite")
    bench_run.add_argument("--ids", nargs="*", default=None)
    bench_run.add_argument("--no-baseline", action="store_true")
    bench_run.add_argument("--parallel", type=int, default=1)
    bench_run.add_argument("--results-dir", default=None)
    bench_run.add_argument("--report", action="store_true")
    bench_run.add_argument(
        "--adapter",
        choices=["scripted", "live"],
        default=None,
        help="Agent adapter (default: scripted; live needs Z_P2_LIVE=1 + hook)",
    )
    bench_score = bench_sub.add_parser(
        "score", help="Score a persisted run without re-executing"
    )
    bench_score.add_argument("results_path")
    bench_list = bench_sub.add_parser("list", help="List benchmark issues")
    bench_list.add_argument("--by-type", action="store_true")

    taxonomy = sub.add_parser(
        "taxonomy",
        help="Bug-concept taxonomy blind-spot candidates (read-only)",
    )
    taxonomy_sub = taxonomy.add_subparsers(dest="taxonomy_command")
    review = taxonomy_sub.add_parser(
        "review",
        help="List evidence_regex gap candidates confirmed across multiple skills",
    )
    review.add_argument(
        "--min-count",
        type=int,
        default=2,
        help="Minimum independently confirmed skills per term (default: 2)",
    )

    skill = sub.add_parser("skill", help="Reusable skills (paste, generate, auto-retrieve)")
    skill_sub = skill.add_subparsers(dest="skill_command")

    add = skill_sub.add_parser("add", help="Paste/import a skill (Z infers metadata)")
    add.add_argument(
        "content",
        nargs="*",
        help="Optional skill markdown (prompted / multi-line if omitted)",
    )
    add.add_argument(
        "--no-sync",
        action="store_true",
        help="Do not sync to the Z backend even if signed in",
    )

    create = skill_sub.add_parser("create", help="Generate and save a skill with your connected model")
    create.add_argument(
        "topic",
        nargs="*",
        help="What the skill should cover (prompted if omitted)",
    )
    create.add_argument(
        "--model",
        default=None,
        help="Override model (otherwise uses AIDER_MODEL / default BYOK model)",
    )
    create.add_argument(
        "--no-sync",
        action="store_true",
        help="Do not sync to the Z backend even if signed in",
    )
    skill_sub.add_parser("list", help="List local and workspace skills")
    show = skill_sub.add_parser("show", help="Show skill metadata (and optional body)")
    show.add_argument("name", nargs="*", help="Skill title or id")
    accept = skill_sub.add_parser(
        "accept",
        help="Accept a draft skill (promote to verified so it can auto-apply)",
    )
    accept.add_argument("name", nargs="*", help="Skill title or id")
    reject = skill_sub.add_parser(
        "reject",
        help="Quarantine a skill (quality_state=rejected — never auto-apply)",
    )
    reject.add_argument("name", nargs="*", help="Skill title or id")
    skill_sub.add_parser("reindex", help="Rebuild the ChromaDB skill vector index")

    # Anything else falls through to the main agent CLI
    return parser


def _skip_account_gate() -> bool:
    return os.environ.get("Z_SKIP_ACCOUNT", "").strip().lower() in ("1", "true", "yes")


def _load_byok_env() -> None:
    """Load ~/.z/byok.env into the process before model checks run."""
    from pathlib import Path

    from dotenv import load_dotenv

    from aider.z.paths import byok_env_path

    path = byok_env_path()
    if Path(path).exists():
        load_dotenv(path, override=True)


def _model_missing_keys(model_id: str | None) -> list[str]:
    if not model_id:
        return []
    try:
        from aider.models import Model

        model = Model(model_id)
        return list(model.missing_keys or [])
    except Exception:
        return []


def _ensure_model_keys(io, model_id: str | None) -> bool:
    """If the preferred model needs API keys that are missing, collect them.

    When a model is already saved, only ask for the missing key(s) — do **not**
    reopen the full foundation-model catalog (that broke the post-login flow).
    """
    if not model_id:
        from aider.z.auth import prompt_byok_setup

        io.tool_output("")
        io.tool_output("No model saved yet — choose a model and paste its API key.")
        return prompt_byok_setup(io)

    missing = _model_missing_keys(model_id)
    if not missing:
        return True

    from aider.z.onboarding import save_byok_key

    io.tool_output("")
    io.tool_output(f"Using saved model: {model_id}")
    io.tool_output(
        f"Needs {', '.join(missing)}. Paste the key below "
        "(or run `z auth switch` to pick a different model)."
    )
    for env_var in missing:
        value = (io.prompt_ask(f"Paste your {env_var}", default="") or "").strip()
        if not value:
            io.tool_error(f"No {env_var} entered — cannot start.")
            return False
        save_byok_key(env_var, value)
        io.tool_output(f"Saved {env_var}.")
    return not _model_missing_keys(model_id)


def _complete_mode_setup(io, mode: str) -> bool:
    """Finish BYOK key setup or router model preference after mode is chosen.

    Only called when the user has just picked a mode (first run or
    ``z auth switch``) — never on every launch once ``auth_mode`` is saved.
    """
    from aider.z.onboarding import save_auth_mode, save_selected_model

    if mode == "byok":
        # Model/key picker is post-auth only. (/app/setup is not shipped yet.)
        from aider.z.auth import prompt_byok_setup

        io.tool_output("")
        io.tool_output("Bring your own key — choose a model and paste its API key.")
        if not prompt_byok_setup(io):
            return False
        save_auth_mode(mode)
        _load_byok_env()
        return True

    if mode == "router":
        from aider.z.login_screen import prompt_router_model_choice

        io.tool_output("")
        io.tool_output(
            "Z's router runs on Z-hosted models. Pick a preferred model — "
            "Z can still escalate to a stronger one when the task needs it."
        )
        io.tool_output(
            "(Hosted routing billing is not live yet — you'll also paste a "
            "provider API key so the agent can run locally.)"
        )
        model_id = prompt_router_model_choice(io)
        if not model_id:
            io.tool_output("Setup cancelled.")
            return False
        save_selected_model(model_id)
        save_auth_mode(mode)
        io.tool_output(f"Router preference saved: {model_id}")
        return _ensure_model_keys(io, model_id)

    return False


def ensure_agent_session(io) -> bool:
    """First-run / session gate for the coding agent.

    Bare ``z`` order:
      1. Not signed in → Google or Z → browser sign-in / sign-up
      2. After account is live, if no saved mode → BYOK vs Z router
      3. BYOK → model + API key; router → preferred Z model
      4. Next launches: reuse saved mode (change only via ``z auth switch``)

    Set Z_SKIP_ACCOUNT=1 to bypass.
    """
    if _skip_account_gate():
        return True

    from aider.z.auth import current_session, open_web_login
    from aider.z.onboarding import load_config

    creds = current_session()
    if not (creds and creds.is_authenticated()):
        # Never show model menus before account auth.
        creds = open_web_login(io)
        if not creds:
            return False

    _load_byok_env()
    config = load_config()
    # Remembered choice — do not re-ask mode on every launch.
    # Still collect missing provider keys so we never fall into Aider's docs prompt.
    if config.auth_mode == "byok":
        return _ensure_model_keys(io, config.selected_model)
    if config.auth_mode == "router" and config.selected_model:
        # Hosted router billing is not live yet — need a provider key to run.
        return _ensure_model_keys(io, config.selected_model)
    if config.auth_mode == "router" and not config.selected_model:
        return _complete_mode_setup(io, "router")

    from aider.z.login_screen import prompt_auth_mode_choice

    mode = prompt_auth_mode_choice(io)
    if mode is None:
        io.tool_output("Setup cancelled.")
        return False
    return _complete_mode_setup(io, mode)


def _apply_web_setup_result(result: dict, *, mode: str) -> None:
    """Persist combined web-flow output: optional credentials + mode data."""
    creds_data = result.get("credentials")
    if creds_data:
        from aider.z.credentials import (
            Credentials,
            apply_credentials_to_env,
            save_credentials,
        )

        creds = Credentials.from_dict(creds_data)
        save_credentials(creds)
        apply_credentials_to_env(creds)

    if mode == "byok":
        mode_result = result.get("mode_result") or {}
        from aider.z.onboarding import save_byok_key, save_selected_model

        if mode_result.get("env_var") and mode_result.get("api_key"):
            save_byok_key(mode_result["env_var"], mode_result["api_key"])
        if mode_result.get("model_id"):
            save_selected_model(mode_result["model_id"])


def _has_explicit_model_flag(argv: list[str]) -> bool:
    return any(a == "--model" or a.startswith("--model=") for a in argv)


def _print_help() -> None:
    build_parser().print_help()
    print(
        "\nAfter install, just run `z`:\n"
        "  not signed in → Google or Z → browser → BYOK vs Z router (saved).\n"
        "  already signed in → reuse your saved BYOK/router choice.\n"
        "Change mode later with `z auth switch`. Agent flags work like `aider …`.\n"
        "Examples:\n"
        "  z\n"
        "  z reset          # redo BYOK vs router + model (stay signed in)\n"
        "  z auth switch    # same as reset, then choose again\n"
        "  z logout\n"
        "  z whoami\n"
        "  z workspace create\n"
        "  z models\n"
        "  z mcp list\n"
        "  z skill add\n"
        "  z skill create \"how this repo validates Stripe webhooks\"\n"
        "  z skill list\n"
        "  z taxonomy review\n"
        "  z benchmark run\n"
        "  z --model sonnet\n"
    )


def _start_agent(argv: list[str]) -> int | None:
    from aider.io import InputOutput

    # Mark this process as the Z CLI so Aider onboarding (OpenRouter "login",
    # "improve aider?" analytics copy) does not replace Z's account login.
    os.environ["Z_CLI"] = "1"
    _load_byok_env()

    # yes=None → actually prompt; yes=False would auto-answer "no" to every ask
    io = InputOutput(pretty=True, fancy_input=True, yes=None)
    if not ensure_agent_session(io):
        return 1

    # Inject persisted preferred model (BYOK or router).
    # try_to_select_default_model() ignores AIDER_MODEL and hardcodes defaults.
    if not _has_explicit_model_flag(argv):
        from aider.z.onboarding import load_config

        config = load_config()
        if config.selected_model and config.auth_mode in ("byok", "router"):
            argv = list(argv) + ["--model", config.selected_model]

    # Avoid Aider's "Open documentation url?" model-warning prompt under Z.
    if "--no-show-model-warnings" not in argv and not any(
        a.startswith("--show-model-warnings") for a in argv
    ):
        argv = list(argv) + ["--no-show-model-warnings"]

    # Final hard gate: never enter the agent with a saved model that still
    # lacks provider keys (that path used to become the Aider docs prompt).
    from aider.z.onboarding import load_config as _load_cfg

    cfg = _load_cfg()
    if cfg.selected_model:
        still_missing = _model_missing_keys(cfg.selected_model)
        if still_missing:
            io.tool_error(
                f"Cannot start: {cfg.selected_model} still needs "
                f"{', '.join(still_missing)}."
            )
            io.tool_output(
                "Run `z auth switch` and paste your key, or:\n"
                f"  export {still_missing[0]}=…"
            )
            return 1

    from aider.main import main as agent_main

    return agent_main(argv=argv)


def main(argv: list[str] | None = None) -> int | None:
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv and argv[0] in ("-h", "--help"):
        _print_help()
        return 0

    top_commands = {
        "login",
        "auth",
        "reset",
        "logout",
        "whoami",
        "workspace",
        "models",
        "mcp",
        "skill",
        "taxonomy",
        "uncertainty",
        "benchmark",
    }

    # Bare `z` (or agent flags) → login if needed, then start the coding agent
    if not argv or argv[0] not in top_commands:
        return _start_agent(argv)

    parser = build_parser()
    args = parser.parse_args(argv)
    return dispatch(args)


def dispatch(args) -> int:
    from aider.io import InputOutput

    # yes=None → actually prompt; yes=False would auto-answer "no" to every ask
    io = InputOutput(pretty=True, fancy_input=True, yes=None)

    if args.command == "login":
        return cmd_login(io, provider=getattr(args, "provider", None))
    if args.command == "auth":
        if getattr(args, "auth_command", None) == "switch":
            return cmd_auth_switch(io)
        # Bare `z auth` remains an alias for login.
        return cmd_login(io, provider=getattr(args, "provider", None))
    if args.command == "reset":
        return cmd_reset(io, logout=bool(getattr(args, "logout", False)))
    if args.command == "logout":
        from aider.z.auth import logout

        logout(io)
        io.tool_output("Tip: run `z reset` if you only want to change BYOK/router/model.")
        return 0
    if args.command == "whoami":
        from aider.z.auth import whoami_text

        io.tool_output(whoami_text())
        return 0
    if args.command == "workspace":
        return cmd_workspace(io, args)
    if args.command == "models":
        return cmd_models(io, search=args.search or "", show_all=args.all)
    if args.command == "mcp":
        return cmd_mcp(io, args)
    if args.command == "skill":
        return cmd_skill(io, args)
    if args.command == "taxonomy":
        return cmd_taxonomy(io, args)
    if args.command == "uncertainty":
        return cmd_uncertainty(io, args)
    if args.command == "benchmark":
        return cmd_benchmark(io, args)
    return 1


def cmd_benchmark(io, args) -> int:
    from aider.z.benchmark.__main__ import main as bench_main

    # Rebuild argv for the benchmark module parser
    sub = getattr(args, "bench_command", None) or "run"
    argv = [sub]
    if sub == "run":
        if getattr(args, "ids", None):
            argv.append("--ids")
            argv.extend(list(args.ids))
        if getattr(args, "no_baseline", False):
            argv.append("--no-baseline")
        if getattr(args, "parallel", None):
            argv.extend(["--parallel", str(args.parallel)])
        if getattr(args, "results_dir", None):
            argv.extend(["--results-dir", str(args.results_dir)])
        if getattr(args, "report", False):
            argv.append("--report")
        if getattr(args, "adapter", None):
            argv.extend(["--adapter", str(args.adapter)])
    elif sub == "score":
        argv.append(args.results_path)
    elif sub == "list":
        if getattr(args, "by_type", False):
            argv.append("--by-type")
    return int(bench_main(argv) or 0)


def cmd_taxonomy(io, args) -> int:
    sub = getattr(args, "taxonomy_command", None) or "review"
    if sub == "review":
        from aider.z.skills.taxonomy_candidates import format_taxonomy_review

        min_count = int(getattr(args, "min_count", 2) or 2)
        io.tool_output(format_taxonomy_review(min_count=min_count).rstrip())
        return 0
    io.tool_error(f"Unknown taxonomy subcommand: {sub}")
    io.tool_output("Usage: z taxonomy review [--min-count N]")
    return 1


def cmd_uncertainty(io, args) -> int:
    sub = getattr(args, "uncertainty_command", None) or "stats"
    if sub == "stats":
        from aider.z.uncertainty.outcomes import format_stats

        io.tool_output(format_stats())
        return 0
    io.tool_error(f"Unknown uncertainty subcommand: {sub}")
    io.tool_output("Usage: z uncertainty stats")
    return 1


def cmd_skill(io, args) -> int:
    from aider.z.skills.cli import (
        accept_skill,
        cmd_skill_add,
        cmd_skill_create,
        cmd_skill_list,
        cmd_skill_reindex,
        cmd_skill_show,
        reject_skill,
    )

    sub = getattr(args, "skill_command", None) or "list"
    if sub == "list":
        return cmd_skill_list(io)
    if sub == "add":
        content = " ".join(getattr(args, "content", None) or []).strip()
        return cmd_skill_add(io, content, sync=not getattr(args, "no_sync", False))
    if sub == "create":
        topic = " ".join(getattr(args, "topic", None) or []).strip()
        return cmd_skill_create(
            io,
            topic,
            model_name=getattr(args, "model", None),
            sync=not getattr(args, "no_sync", False),
        )
    if sub == "show":
        name = " ".join(getattr(args, "name", None) or []).strip()
        return cmd_skill_show(io, name)
    if sub == "accept":
        name = " ".join(getattr(args, "name", None) or []).strip()
        return accept_skill(io, name)
    if sub == "reject":
        name = " ".join(getattr(args, "name", None) or []).strip()
        return reject_skill(io, name)
    if sub == "reindex":
        return cmd_skill_reindex(io)
    io.tool_error(f"Unknown skill subcommand: {sub}")
    io.tool_output(
        "Usage: z skill add | create [topic…] | list | show <name> | "
        "accept <name> | reject <name> | reindex"
    )
    return 1


def cmd_mcp(io, args) -> int:
    from aider.z.mcp_client import print_mcp_list

    sub = getattr(args, "mcp_command", None) or "list"
    if sub == "list":
        print_mcp_list(io)
        return 0
    io.tool_error(f"Unknown mcp subcommand: {sub}")
    io.tool_output("Usage: z mcp list")
    return 1


def cmd_auth_switch(io) -> int:
    """Re-choose BYOK vs router (+ model/key). Clears the previous saved choice."""
    from aider.z.auth import current_session, open_web_login
    from aider.z.onboarding import clear_setup

    creds = current_session()
    if not (creds and creds.is_authenticated()):
        creds = open_web_login(io)
        if not creds:
            return 1

    clear_setup(clear_keys=True)
    io.tool_output("Cleared previous BYOK/router/model choice.")

    from aider.z.login_screen import prompt_auth_mode_choice

    mode = prompt_auth_mode_choice(io)
    if mode is None:
        io.tool_output("Switch cancelled.")
        return 1
    return 0 if _complete_mode_setup(io, mode) else 1


def cmd_reset(io, *, logout: bool = False) -> int:
    """Clear saved setup choices and pick BYOK/router/model again.

    Keeps the Z account signed in unless ``--logout`` is passed.
    """
    from aider.z.onboarding import clear_setup

    if logout:
        from aider.z.auth import logout as do_logout

        do_logout(io)
        clear_setup(clear_keys=True)
        io.tool_output("Setup cleared. Run `z` to sign in and choose again.")
        return 0

    from aider.z.auth import current_session, open_web_login

    clear_setup(clear_keys=True)
    io.tool_output("Cleared saved BYOK/router/model choices (account login kept).")

    creds = current_session()
    if not (creds and creds.is_authenticated()):
        creds = open_web_login(io)
        if not creds:
            return 1

    from aider.z.login_screen import prompt_auth_mode_choice

    mode = prompt_auth_mode_choice(io)
    if mode is None:
        io.tool_output("Reset cancelled — run `z reset` again when ready.")
        return 1
    return 0 if _complete_mode_setup(io, mode) else 1


def cmd_workspace(io, args) -> int:
    sub = getattr(args, "workspace_command", None)
    from aider.z.auth import current_session
    from aider.z.credentials import WorkspaceContext, save_credentials
    from aider.z.workspace_cli import (
        WorkspaceError,
        create_workspace,
        invite_member,
        list_members,
    )

    if sub == "create":
        name = (
            " ".join(args.name).strip()
            if args.name
            else (io.prompt_ask("Workspace name") or "").strip()
        )
        if not name:
            io.tool_error("Workspace name required.")
            return 1
        try:
            ws = create_workspace(name, organization=args.organization)
        except WorkspaceError as err:
            io.tool_error(str(err))
            return 1
        # Persist the new workspace onto the current session so subsequent
        # `z` launches sync into it automatically (engine.py reads
        # creds.workspace.id).
        creds = current_session()
        if creds:
            if not creds.workspace:
                creds.workspace = WorkspaceContext()
            creds.workspace.id = ws.get("id")
            creds.workspace.name = ws.get("name")
            creds.workspace.role = ws.get("role") or creds.workspace.role
            creds.workspace.organization = ws.get("organization")
            save_credentials(creds)
        io.tool_output(f"Workspace '{name}' created.")
        return 0

    if sub == "invite":
        creds = current_session()
        if not creds or not creds.workspace or not creds.workspace.id:
            io.tool_error("No active workspace — run `z workspace create` first.")
            return 1
        identifier = (
            " ".join(args.identifier).strip()
            if args.identifier
            else (io.prompt_ask("Email or phone to invite") or "").strip()
        )
        if not identifier:
            io.tool_error("Email or phone required.")
            return 1
        try:
            invite_member(creds.workspace.id, identifier)
        except WorkspaceError as err:
            io.tool_error(str(err))
            return 1
        io.tool_output(f"Invited {identifier}.")
        return 0

    if sub == "members":
        creds = current_session()
        if not creds or not creds.workspace or not creds.workspace.id:
            io.tool_error("No active workspace.")
            return 1
        try:
            members = list_members(creds.workspace.id)
        except WorkspaceError as err:
            io.tool_error(str(err))
            return 1
        if not members:
            io.tool_output("No members found.")
            return 0
        for m in members:
            label = m.get("name") or m.get("email") or m.get("phone") or m.get("id")
            io.tool_output(f"  {label} ({m.get('role', 'member')})")
        return 0

    if sub == "switch":
        # Multi-workspace membership needs a backend data-model decision
        # (Credentials.workspace is still a single object). Don't invent
        # a list schema client-side until that exists.
        io.tool_error(
            "Workspace switch is not available yet — "
            "multi-workspace membership is not supported in this client."
        )
        return 1

    io.tool_error(f"Unknown workspace subcommand: {sub}")
    io.tool_output("Usage: z workspace create | invite | members | switch")
    return 1


def cmd_login(io, provider: str | None = None) -> int:
    """Optional re-auth. First-time sign-in is just bare ``z``."""
    from aider.z.auth import current_session, open_web_login

    _ = provider  # Google / Z chosen in the terminal → web page
    io.tool_output(
        "Tip: after install you can just run `z` — sign-in only appears if needed."
    )
    creds = open_web_login(io)
    if not creds:
        return 1
    # If mode was never chosen, finish the same post-auth prompts as bare `z`.
    from aider.z.onboarding import load_config

    config = load_config()
    if config.auth_mode not in ("byok", "router") or (
        config.auth_mode == "router" and not config.selected_model
    ):
        if not ensure_agent_session(io):
            return 1
    else:
        who = current_session()
        if who:
            io.tool_output(f"Signed in as {who.display_name()}.")
    return 0


def cmd_models(io, search: str = "", show_all: bool = False) -> int:
    from aider.z.models_catalog import print_curated_models, print_search_models

    if show_all or search:
        print_search_models(io, search or "")
    else:
        print_curated_models(io)
    return 0


if __name__ == "__main__":
    raise SystemExit(main() or 0)
