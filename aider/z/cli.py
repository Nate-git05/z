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

    login = sub.add_parser("login", help="Sign in to your Z account")
    login.add_argument(
        "--provider",
        choices=["email", "phone", "google"],
        default=None,
        help="Skip the menu and use this provider",
    )

    auth = sub.add_parser(
        "auth",
        help="Account auth (default: login) or re-choose BYOK vs Z router",
    )
    auth_sub = auth.add_subparsers(dest="auth_command")
    auth_sub.add_parser(
        "switch",
        help="Re-choose between BYOK and Z router sign-in",
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


def ensure_agent_session(io) -> bool:
    """Require a Z account, then (once) BYOK vs router model-source choice.

    Account login is always required — including for BYOK users — so
    workspace/team features have an identity. Model-source choice is a
    separate, persisted step after login.

    Set Z_SKIP_ACCOUNT=1 to bypass (automation / tests).
    """
    if _skip_account_gate():
        return True

    from aider.z.auth import current_session, run_login_flow

    # Step 1: account login is ALWAYS required, regardless of BYOK/router.
    creds = current_session()
    if not (creds and creds.is_authenticated()):
        creds = run_login_flow(io)
        if not creds:
            return False

    # Step 2: BYOK vs router — asked once, then completed in the browser
    # (local-callback pattern, same as Google OAuth). Dev mode falls back
    # to an in-terminal picker when no auth backend is configured.
    from aider.z.onboarding import load_config, save_auth_mode

    config = load_config()
    if config.auth_mode in ("byok", "router"):
        return True

    from aider.z.auth import apply_byok_setup_result, open_web_setup
    from aider.z.login_screen import prompt_auth_mode_choice

    mode = prompt_auth_mode_choice(io)
    if mode is None:
        io.tool_output("Setup cancelled.")
        return False

    result = open_web_setup(io, mode)
    if result is None:
        return False

    if mode == "byok":
        apply_byok_setup_result(result)
        save_auth_mode("byok")
        return True

    save_auth_mode("router")
    return True


def _has_explicit_model_flag(argv: list[str]) -> bool:
    return any(a == "--model" or a.startswith("--model=") for a in argv)


def _print_help() -> None:
    build_parser().print_help()
    print(
        "\nRun `z` with no arguments to sign in (if needed) and start the coding agent.\n"
        "Any other arguments are passed through to the agent (same as `aider …`).\n"
        "Examples:\n"
        "  z\n"
        "  z login\n"
        "  z auth switch\n"
        "  z workspace create\n"
        "  z workspace invite\n"
        "  z workspace members\n"
        "  z models\n"
        "  z mcp list\n"
        "  z skill add\n"
        "  z skill create \"how this repo validates Stripe webhooks\"\n"
        "  z skill list\n"
        "  z skill show stripe\n"
        "  z skill accept stripe\n"
        "  z taxonomy review\n"
        "  z uncertainty stats\n"
        "  z --model sonnet\n"
    )


def _start_agent(argv: list[str]) -> int | None:
    from aider.io import InputOutput

    # Mark this process as the Z CLI so Aider onboarding (OpenRouter "login",
    # "improve aider?" analytics copy) does not replace Z's account login.
    os.environ["Z_CLI"] = "1"

    # yes=None → actually prompt; yes=False would auto-answer "no" to every ask
    io = InputOutput(pretty=True, fancy_input=True, yes=None)
    if not ensure_agent_session(io):
        return 1

    # Inject persisted BYOK model — try_to_select_default_model() ignores
    # AIDER_MODEL and hardcodes per-provider defaults (e.g. always "sonnet").
    if not _has_explicit_model_flag(argv):
        from aider.z.onboarding import load_config

        config = load_config()
        if config.auth_mode == "byok" and config.selected_model:
            argv = list(argv) + ["--model", config.selected_model]

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
        "logout",
        "whoami",
        "workspace",
        "models",
        "mcp",
        "skill",
        "taxonomy",
        "uncertainty",
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
    if args.command == "logout":
        from aider.z.auth import logout

        logout(io)
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
    return 1


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
    """Re-choose BYOK vs router without forcing a fresh account login."""
    from aider.z.auth import (
        apply_byok_setup_result,
        current_session,
        open_web_setup,
        run_login_flow,
    )
    from aider.z.login_screen import prompt_auth_mode_choice
    from aider.z.onboarding import save_auth_mode

    creds = current_session()
    if not (creds and creds.is_authenticated()):
        creds = run_login_flow(io)
        if not creds:
            return 1

    mode = prompt_auth_mode_choice(io)
    if mode is None:
        io.tool_output("Switch cancelled.")
        return 1

    result = open_web_setup(io, mode)
    if result is None:
        return 1

    if mode == "byok":
        apply_byok_setup_result(result)
        save_auth_mode("byok")
        return 0

    save_auth_mode("router")
    io.tool_output("Using Z's router for model access.")
    return 0


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
    from aider.z import auth

    # Force-enable interactive prompts
    if provider == "email":
        try:
            result = auth.login_with_email(io)
        except auth.AuthError as err:
            io.tool_error(str(err))
            return 1
        if result.ok and result.credentials:
            auth.save_credentials(result.credentials)
            auth.apply_credentials_to_env(result.credentials)
            io.tool_output(f"Signed in as {result.credentials.display_name()}.")
            return 0
        io.tool_error(result.message or "Login failed.")
        return 1
    if provider == "phone":
        try:
            result = auth.login_with_phone(io)
        except auth.AuthError as err:
            io.tool_error(str(err))
            return 1
        if result.ok and result.credentials:
            auth.save_credentials(result.credentials)
            auth.apply_credentials_to_env(result.credentials)
            io.tool_output(f"Signed in as {result.credentials.display_name()}.")
            return 0
        io.tool_error(result.message or "Login failed.")
        return 1
    if provider == "google":
        try:
            result = auth.login_with_google(io)
        except auth.AuthError as err:
            io.tool_error(str(err))
            return 1
        if result.ok and result.credentials:
            auth.save_credentials(result.credentials)
            auth.apply_credentials_to_env(result.credentials)
            io.tool_output(f"Signed in as {result.credentials.display_name()}.")
            return 0
        io.tool_error(result.message or "Login failed.")
        return 1

    creds = auth.run_login_flow(io)
    return 0 if creds else 1


def cmd_models(io, search: str = "", show_all: bool = False) -> int:
    from aider.z.models_catalog import print_curated_models, print_search_models

    if show_all or search:
        print_search_models(io, search or "")
    else:
        print_curated_models(io)
    return 0


if __name__ == "__main__":
    raise SystemExit(main() or 0)
