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

    sub.add_parser("auth", help="Alias for `z login`")
    sub.add_parser("logout", help="Sign out and clear ~/.z/credentials")
    sub.add_parser("whoami", help="Show the current Z account / workspace")

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
    skill_sub.add_parser("reindex", help="Rebuild the ChromaDB skill vector index")

    # Anything else falls through to the main agent CLI
    return parser


def _skip_account_gate() -> bool:
    return os.environ.get("Z_SKIP_ACCOUNT", "").strip().lower() in ("1", "true", "yes")


def ensure_agent_session(io) -> bool:
    """Require a Z account before starting the coding agent.

    Shows the branded login flow when the user is not signed in.
    Set Z_SKIP_ACCOUNT=1 to bypass (automation / tests).
    """
    if _skip_account_gate():
        return True

    from aider.z.auth import current_session, run_login_flow

    creds = current_session()
    if creds and creds.is_authenticated():
        return True

    creds = run_login_flow(io)
    return bool(creds)


def _print_help() -> None:
    build_parser().print_help()
    print(
        "\nRun `z` with no arguments to sign in (if needed) and start the coding agent.\n"
        "Any other arguments are passed through to the agent (same as `aider …`).\n"
        "Examples:\n"
        "  z\n"
        "  z login\n"
        "  z models\n"
        "  z mcp list\n"
        "  z skill add\n"
        "  z skill create \"how this repo validates Stripe webhooks\"\n"
        "  z skill list\n"
        "  z skill show stripe\n"
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

    from aider.main import main as agent_main

    return agent_main(argv=argv)


def main(argv: list[str] | None = None) -> int | None:
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv and argv[0] in ("-h", "--help"):
        _print_help()
        return 0

    top_commands = {"login", "auth", "logout", "whoami", "models", "mcp", "skill"}

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

    if args.command in ("login", "auth"):
        return cmd_login(io, provider=getattr(args, "provider", None))
    if args.command == "logout":
        from aider.z.auth import logout

        logout(io)
        return 0
    if args.command == "whoami":
        from aider.z.auth import whoami_text

        io.tool_output(whoami_text())
        return 0
    if args.command == "models":
        return cmd_models(io, search=args.search or "", show_all=args.all)
    if args.command == "mcp":
        return cmd_mcp(io, args)
    if args.command == "skill":
        return cmd_skill(io, args)
    return 1


def cmd_skill(io, args) -> int:
    from aider.z.skills.cli import (
        cmd_skill_add,
        cmd_skill_create,
        cmd_skill_list,
        cmd_skill_reindex,
        cmd_skill_show,
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
    if sub == "reindex":
        return cmd_skill_reindex(io)
    io.tool_error(f"Unknown skill subcommand: {sub}")
    io.tool_output("Usage: z skill add | create [topic…] | list | show <name> | reindex")
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
