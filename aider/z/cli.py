"""Top-level `z` CLI — login/auth/models and passthrough to the coding agent."""

from __future__ import annotations

import argparse
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

    # Anything else falls through to the main agent CLI
    return parser


def main(argv: list[str] | None = None) -> int | None:
    argv = list(sys.argv[1:] if argv is None else argv)

    # No subcommand / unknown → hand off to the full agent CLI (aider.main)
    if not argv or argv[0] in ("-h", "--help"):
        # Show z help plus note about agent passthrough
        build_parser().print_help()
        print(
            "\nAny other arguments are passed through to the Z coding agent"
            " (same as `aider …`).\n"
            "Examples:\n"
            "  z login\n"
            "  z models\n"
            "  z --model sonnet\n"
        )
        return 0

    top_commands = {"login", "auth", "logout", "whoami", "models"}
    if argv[0] not in top_commands:
        from aider.main import main as agent_main

        return agent_main(argv=argv)

    parser = build_parser()
    args = parser.parse_args(argv)
    return dispatch(args)


def dispatch(args) -> int:
    from aider.io import InputOutput

    io = InputOutput(pretty=True, fancy_input=True, yes=False)

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
