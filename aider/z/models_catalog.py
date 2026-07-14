"""Curated current model catalog for Z, sourced from provider docs at build time.

Model identifiers below were taken from official documentation on 2026-07-14:
  - Anthropic: https://platform.claude.com/docs/en/about-claude/models/overview
  - OpenAI:    https://developers.openai.com/api/docs/models
               https://developers.openai.com/api/docs/models/all
               https://developers.openai.com/api/docs/models/gpt-5.3-codex

Account auth is independent of these — users bring their own API keys (BYOK).
"""

from __future__ import annotations

# Exact Claude API IDs from Anthropic "Models overview" (current + key legacy).
ANTHROPIC_CURRENT = [
    # Current (docs table)
    "claude-fable-5",
    "claude-opus-4-8",
    "claude-sonnet-5",
    "claude-haiku-4-5-20251001",
    "claude-haiku-4-5",  # alias
    # Legacy still listed by Anthropic
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-sonnet-4-5-20250929",
    "claude-sonnet-4-5",
    "claude-opus-4-5-20251101",
    "claude-opus-4-5",
    "claude-opus-4-1-20250805",  # deprecated
    "claude-opus-4-1",
]

# Exact OpenAI model IDs from OpenAI Models docs (frontier + coding-relevant).
OPENAI_CURRENT = [
    # GPT-5.6 family (frontier)
    "gpt-5.6-sol",
    "gpt-5.6",  # alias → gpt-5.6-sol
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    # Recent GPT-5.x
    "gpt-5.5",
    "gpt-5.5-pro",
    "gpt-5.4",
    "gpt-5.4-pro",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
    "gpt-5.3",
    "gpt-5.3-pro",
    "gpt-5.3-codex",  # coding-optimized (Codex successor line)
    "gpt-5.2",
    "gpt-5.2-pro",
    "gpt-5.1",
    "gpt-5",
    "gpt-5-mini",
    "gpt-5-nano",
    # Prior still-listed
    "gpt-4.1",
    "gpt-4.1-mini",
    "o3",
    "o3-pro",
    "gpt-4o",
    "gpt-4o-mini",
]

CURATED_SECTIONS = [
    ("Anthropic (Claude)", ANTHROPIC_CURRENT),
    ("OpenAI", OPENAI_CURRENT),
]


def print_curated_models(io) -> None:
    io.tool_output("Z curated models (from provider docs)")
    io.tool_output("Bring your own API key — account login is separate from model keys.")
    io.tool_output("")
    for title, models in CURATED_SECTIONS:
        io.tool_output(f"{title}:")
        for name in models:
            io.tool_output(f"  - {name}")
        io.tool_output("")
    io.tool_output("Aliases: sonnet, opus, haiku, fable, gpt-5.6, 4o, codex, …")
    io.tool_output("Search all known models:  z models <text>   or   /models <text>")
    io.tool_output("Select a model:           z --model <name>  or   /model <name>")


def print_search_models(io, search: str) -> None:
    from aider import models

    if not search.strip():
        # Empty search with --all: show curated + hint
        print_curated_models(io)
        io.tool_output("Tip: pass a search string, e.g. `z models claude` or `z models gpt-5.6`")
        return
    models.print_matching_models(io, search)
