#!/usr/bin/env python

import base64
import hashlib
import json
import locale
import math
import mimetypes
import os
import platform
import re
import sys
import threading
import time
import traceback
from collections import defaultdict
from datetime import datetime

# Optional dependency: used to convert locale codes (eg ``en_US``)
# into human-readable language names (eg ``English``).
try:
    from babel import Locale  # type: ignore
except ImportError:  # Babel not installed – we will fall back to a small mapping
    Locale = None
from json.decoder import JSONDecodeError
from pathlib import Path
from typing import List, Optional

from rich.console import Console

from aider import __version__, models, prompts, urls, utils
from aider.analytics import Analytics
from aider.commands import Commands
from aider.exceptions import LiteLLMExceptions
from aider.history import ChatSummary
from aider.io import ConfirmGroup, InputOutput
from aider.linter import Linter
from aider.llm import litellm
from aider.models import RETRY_TIMEOUT
from aider.reasoning_tags import (
    REASONING_TAG,
    format_reasoning_content,
    remove_reasoning_content,
    replace_reasoning_tags,
)
from aider.repo import ANY_GIT_ERROR, GitRepo
from aider.repomap import RepoMap
from aider.run_cmd import run_cmd
from aider.utils import format_content, format_messages, format_tokens, is_image_file
from aider.waiting import WaitingSpinner
from aider.z.waiting_game import waiting_display

from ..dump import dump  # noqa: F401
from .chat_chunks import ChatChunks


class UnknownEditFormat(ValueError):
    def __init__(self, edit_format, valid_formats):
        self.edit_format = edit_format
        self.valid_formats = valid_formats
        super().__init__(
            f"Unknown edit format {edit_format}. Valid formats are: {', '.join(valid_formats)}"
        )


class MissingAPIKeyError(ValueError):
    pass


class FinishReasonLength(Exception):
    pass


def wrap_fence(name):
    return f"<{name}>", f"</{name}>"


all_fences = [
    ("`" * 3, "`" * 3),
    ("`" * 4, "`" * 4),  # LLMs ignore and revert to triple-backtick, causing #2879
    wrap_fence("source"),
    wrap_fence("code"),
    wrap_fence("pre"),
    wrap_fence("codeblock"),
    wrap_fence("sourcecode"),
]


class Coder:
    abs_fnames = None
    abs_read_only_fnames = None
    repo = None
    last_aider_commit_hash = None
    aider_edited_files = None
    last_asked_for_commit_time = 0
    repo_map = None
    functions = None
    num_exhausted_context_windows = 0
    num_malformed_responses = 0
    last_keyboard_interrupt = None
    num_reflections = 0
    max_reflections = 3
    edit_format = None
    yield_stream = False
    temperature = None
    auto_lint = True
    auto_test = False
    test_cmd = None
    lint_outcome = None
    test_outcome = None
    multi_response_content = ""
    partial_response_content = ""
    commit_before_message = []
    message_cost = 0.0
    add_cache_headers = False
    cache_warming_thread = None
    num_cache_warming_pings = 0
    suggest_shell_commands = True
    detect_urls = True
    ignore_mentions = None
    chat_language = None
    commit_language = None
    file_watcher = None
    task_mode = None  # aider.z.task_mode.TaskMode — per-message, not sticky
    forced_task_mode = None  # sticky TaskMode from /plan (and one-shot /ask|/plan with args)
    task_intent = None  # aider.z.uncertainty.intent.TaskIntent
    show_cost = False  # P1: print Tokens/Cost when True (also Z_SHOW_USAGE / io.show_cost)
    _shell_class_approvals = None  # session: risk_class → bool

    @classmethod
    def create(
        self,
        main_model=None,
        edit_format=None,
        io=None,
        from_coder=None,
        summarize_from_coder=True,
        **kwargs,
    ):
        import aider.coders as coders

        if not main_model:
            if from_coder:
                main_model = from_coder.main_model
            else:
                main_model = models.Model(models.DEFAULT_MODEL_NAME)

        if edit_format == "code":
            edit_format = None
        if edit_format is None:
            if from_coder:
                edit_format = from_coder.edit_format
            else:
                edit_format = main_model.edit_format

        if not io and from_coder:
            io = from_coder.io

        if from_coder:
            use_kwargs = dict(from_coder.original_kwargs)  # copy orig kwargs

            # If the edit format changes, we can't leave old ASSISTANT
            # messages in the chat history. The old edit format will
            # confused the new LLM. It may try and imitate it, disobeying
            # the system prompt.
            done_messages = from_coder.done_messages
            if edit_format != from_coder.edit_format and done_messages and summarize_from_coder:
                try:
                    done_messages = from_coder.summarizer.summarize_all(done_messages)
                except ValueError:
                    # If summarization fails, keep the original messages and warn the user
                    io.tool_warning(
                        "Chat history summarization failed, continuing with full history"
                    )

            # Bring along context from the old Coder
            update = dict(
                fnames=list(from_coder.abs_fnames),
                read_only_fnames=list(from_coder.abs_read_only_fnames),  # Copy read-only files
                done_messages=done_messages,
                cur_messages=from_coder.cur_messages,
                aider_commit_hashes=from_coder.aider_commit_hashes,
                commands=from_coder.commands.clone(),
                total_cost=from_coder.total_cost,
                ignore_mentions=from_coder.ignore_mentions,
                total_tokens_sent=from_coder.total_tokens_sent,
                total_tokens_received=from_coder.total_tokens_received,
                file_watcher=from_coder.file_watcher,
            )
            use_kwargs.update(update)  # override to complete the switch
            use_kwargs.update(kwargs)  # override passed kwargs

            kwargs = use_kwargs
            from_coder.ok_to_warm_cache = False

        for coder in coders.__all__:
            if hasattr(coder, "edit_format") and coder.edit_format == edit_format:
                res = coder(main_model, io, **kwargs)
                res.original_kwargs = dict(kwargs)
                # Preserve sticky plan / cost chrome across SwitchCoder
                if from_coder is not None:
                    ft = getattr(from_coder, "forced_task_mode", None)
                    if ft is not None:
                        res.forced_task_mode = ft
                    if getattr(from_coder, "show_cost", False):
                        res.show_cost = True
                    for attr in (
                        "_plan_interview_stage",
                        "_plan_clarify_asked",
                        "_active_plan_path",
                    ):
                        if hasattr(from_coder, attr):
                            setattr(res, attr, getattr(from_coder, attr))
                return res

        valid_formats = [
            str(c.edit_format)
            for c in coders.__all__
            if hasattr(c, "edit_format") and c.edit_format is not None
        ]
        raise UnknownEditFormat(edit_format, valid_formats)

    def clone(self, **kwargs):
        new_coder = Coder.create(from_coder=self, **kwargs)
        return new_coder

    def get_announcements(self):
        lines = []
        use_z = getattr(self.io, "z_theme", True)
        if use_z:
            lines.append(f"Z v{__version__}")
        else:
            lines.append(f"Aider v{__version__}")

        # Model
        main_model = self.main_model
        weak_model = main_model.weak_model

        if weak_model is not main_model:
            prefix = "Main model"
        else:
            prefix = "Model"

        output = f"{prefix}: {main_model.name} with {self.edit_format} edit format"

        # Check for thinking token budget
        thinking_tokens = main_model.get_thinking_tokens()
        if thinking_tokens:
            output += f", {thinking_tokens} think tokens"

        # Check for reasoning effort
        reasoning_effort = main_model.get_reasoning_effort()
        if reasoning_effort:
            output += f", reasoning {reasoning_effort}"

        if self.add_cache_headers or main_model.caches_by_default:
            output += ", prompt cache"
        if main_model.info.get("supports_assistant_prefill"):
            output += ", infinite output"

        lines.append(output)

        if self.edit_format == "architect":
            output = (
                f"Editor model: {main_model.editor_model.name} with"
                f" {main_model.editor_edit_format} edit format"
            )
            lines.append(output)

        if weak_model is not main_model:
            output = f"Weak model: {weak_model.name}"
            lines.append(output)

        # Repo
        if self.repo:
            rel_repo_dir = self.repo.get_rel_repo_dir()
            num_files = len(self.repo.get_tracked_files())

            lines.append(f"Git repo: {rel_repo_dir} with {num_files:,} files")
            if num_files > 1000:
                lines.append(
                    "Warning: For large repos, consider using --subtree-only and .aiderignore"
                )
                lines.append(f"See: {urls.large_repos}")
        else:
            lines.append("Git repo: none")

        # Repo-map
        if self.repo_map:
            map_tokens = self.repo_map.max_map_tokens
            if map_tokens > 0:
                refresh = self.repo_map.refresh
                lines.append(f"Repo-map: using {map_tokens} tokens, {refresh} refresh")
                max_map_tokens = self.main_model.get_repo_map_tokens() * 2
                if map_tokens > max_map_tokens:
                    lines.append(
                        f"Warning: map-tokens > {max_map_tokens} is not recommended. Too much"
                        " irrelevant code can confuse LLMs."
                    )
            else:
                lines.append("Repo-map: disabled because map_tokens == 0")
        else:
            lines.append("Repo-map: disabled")

        # Files
        for fname in self.get_inchat_relative_files():
            lines.append(f"Added {fname} to the chat.")

        for fname in self.abs_read_only_fnames:
            rel_fname = self.get_rel_fname(fname)
            lines.append(f"Added {rel_fname} to the chat (read-only).")

        if self.done_messages:
            lines.append("Restored previous conversation history.")

        if self.io.multiline_mode:
            lines.append("Multiline mode: Enabled. Enter inserts newline, Alt-Enter submits text")

        return lines

    ok_to_warm_cache = False

    def __init__(
        self,
        main_model,
        io,
        repo=None,
        fnames=None,
        add_gitignore_files=False,
        read_only_fnames=None,
        show_diffs=False,
        auto_commits=True,
        dirty_commits=True,
        dry_run=False,
        map_tokens=1024,
        verbose=False,
        stream=True,
        use_git=True,
        cur_messages=None,
        done_messages=None,
        restore_chat_history=False,
        auto_lint=True,
        auto_test=False,
        lint_cmds=None,
        test_cmd=None,
        aider_commit_hashes=None,
        map_mul_no_files=8,
        commands=None,
        summarizer=None,
        total_cost=0.0,
        analytics=None,
        map_refresh="auto",
        cache_prompts=False,
        num_cache_warming_pings=0,
        suggest_shell_commands=True,
        chat_language=None,
        commit_language=None,
        detect_urls=True,
        ignore_mentions=None,
        total_tokens_sent=0,
        total_tokens_received=0,
        file_watcher=None,
        auto_copy_context=False,
        auto_accept_architect=True,
        verify_commit_gate=True,
        force_commit=False,
    ):
        # Fill in a dummy Analytics if needed, but it is never .enable()'d
        self.analytics = analytics if analytics is not None else Analytics()

        self.event = self.analytics.event
        self.chat_language = chat_language
        self.commit_language = commit_language
        self.commit_before_message = []
        self.verify_commit_gate = verify_commit_gate
        self.force_commit = force_commit
        self.aider_commit_hashes = set()
        self.rejected_urls = set()
        self.abs_root_path_cache = {}

        self.auto_copy_context = auto_copy_context
        self.auto_accept_architect = auto_accept_architect

        self.ignore_mentions = ignore_mentions
        if not self.ignore_mentions:
            self.ignore_mentions = set()

        self.file_watcher = file_watcher
        if self.file_watcher:
            self.file_watcher.coder = self

        self.suggest_shell_commands = suggest_shell_commands
        self.detect_urls = detect_urls

        self.num_cache_warming_pings = num_cache_warming_pings

        if not fnames:
            fnames = []

        if io is None:
            io = InputOutput()

        if aider_commit_hashes:
            self.aider_commit_hashes = aider_commit_hashes
        else:
            self.aider_commit_hashes = set()

        self.chat_completion_call_hashes = []
        self.chat_completion_response_hashes = []
        self.need_commit_before_edits = set()

        self.total_cost = total_cost
        self.total_tokens_sent = total_tokens_sent
        self.total_tokens_received = total_tokens_received
        self.message_tokens_sent = 0
        self.message_tokens_received = 0

        self.verbose = verbose
        self.abs_fnames = set()
        self.abs_read_only_fnames = set()
        self.add_gitignore_files = add_gitignore_files

        if cur_messages:
            self.cur_messages = cur_messages
        else:
            self.cur_messages = []

        if done_messages:
            self.done_messages = done_messages
        else:
            self.done_messages = []

        self.io = io

        self.shell_commands = []

        if not auto_commits:
            dirty_commits = False

        self.auto_commits = auto_commits
        self.dirty_commits = dirty_commits

        self.dry_run = dry_run
        self.pretty = self.io.pretty

        self.main_model = main_model
        # Set the reasoning tag name based on model settings or default
        self.reasoning_tag_name = (
            self.main_model.reasoning_tag if self.main_model.reasoning_tag else REASONING_TAG
        )

        self.stream = stream and main_model.streaming

        if cache_prompts and self.main_model.cache_control:
            self.add_cache_headers = True

        self.show_diffs = show_diffs

        self.commands = commands or Commands(self.io, self)
        self.commands.coder = self

        self.repo = repo
        if use_git and self.repo is None:
            try:
                self.repo = GitRepo(
                    self.io,
                    fnames,
                    None,
                    models=main_model.commit_message_models(),
                )
            except FileNotFoundError:
                pass

        if self.repo:
            self.root = self.repo.root

        for fname in fnames:
            fname = Path(fname)
            if self.repo and self.repo.git_ignored_file(fname) and not self.add_gitignore_files:
                self.io.tool_warning(f"Skipping {fname} that matches gitignore spec.")
                continue

            if self.repo and self.repo.ignored_file(fname):
                self.io.tool_warning(f"Skipping {fname} that matches aiderignore spec.")
                continue

            if not fname.exists():
                if utils.touch_file(fname):
                    self.io.tool_output(f"Creating empty file {fname}")
                else:
                    self.io.tool_warning(f"Can not create {fname}, skipping.")
                    continue

            if not fname.is_file():
                self.io.tool_warning(f"Skipping {fname} that is not a normal file.")
                continue

            fname = str(fname.resolve())

            self.abs_fnames.add(fname)
            self.check_added_files()

        if not self.repo:
            self.root = utils.find_common_root(self.abs_fnames)

        if read_only_fnames:
            self.abs_read_only_fnames = set()
            for fname in read_only_fnames:
                abs_fname = self.abs_root_path(fname)
                if os.path.exists(abs_fname):
                    self.abs_read_only_fnames.add(abs_fname)
                else:
                    self.io.tool_warning(f"Error: Read-only file {fname} does not exist. Skipping.")

        if map_tokens is None:
            use_repo_map = main_model.use_repo_map
            map_tokens = 1024
        else:
            use_repo_map = map_tokens > 0

        max_inp_tokens = self.main_model.info.get("max_input_tokens") or 0

        has_map_prompt = hasattr(self, "gpt_prompts") and self.gpt_prompts.repo_content_prefix

        if use_repo_map and self.repo and has_map_prompt:
            self.repo_map = RepoMap(
                map_tokens,
                self.root,
                self.main_model,
                io,
                self.gpt_prompts.repo_content_prefix,
                self.verbose,
                max_inp_tokens,
                map_mul_no_files=map_mul_no_files,
                refresh=map_refresh,
            )

        self.summarizer = summarizer or ChatSummary(
            [self.main_model.weak_model, self.main_model],
            self.main_model.max_chat_history_tokens,
        )

        self.summarizer_thread = None
        self.summarized_done_messages = []
        self.summarizing_messages = None

        if not self.done_messages and restore_chat_history:
            history_md = self.io.read_text(self.io.chat_history_file)
            if history_md:
                self.done_messages = utils.split_chat_history_markdown(history_md)
                self.summarize_start()

        # Linting and testing
        self.linter = Linter(root=self.root, encoding=io.encoding)
        self.auto_lint = auto_lint
        self.setup_lint_cmds(lint_cmds)
        self.lint_cmds = lint_cmds
        self.auto_test = auto_test
        self.test_cmd = test_cmd

        # validate the functions jsonschema
        if self.functions:
            from jsonschema import Draft7Validator

            for function in self.functions:
                Draft7Validator.check_schema(function)

            if self.verbose:
                self.io.tool_output("JSON Schema:")
                self.io.tool_output(json.dumps(self.functions, indent=4))

        # Z uncertainty tree — structured risk/confidence nodes for this session
        self.uncertainty_engine = None
        self.uncertainty_store = None
        self.last_verification = None
        self._z_verify_gen_attempts = 0
        self._z_verify_fix_attempts = 0
        self._z_auto_act_attempts = 0
        try:
            from aider.z.uncertainty.engine import attach_engine_to_coder

            user_label = None
            try:
                from aider.z.auth import current_session

                creds = current_session()
                if creds:
                    user_label = creds.display_name()
            except Exception:
                pass
            attach_engine_to_coder(self, user_label=user_label)
        except Exception:
            pass

    def setup_lint_cmds(self, lint_cmds):
        if not lint_cmds:
            return
        for lang, cmd in lint_cmds.items():
            self.linter.set_linter(lang, cmd)

    def show_announcements(self):
        use_z = getattr(self.io, "z_theme", True)
        lines = self.get_announcements()
        if use_z and self.io.pretty:
            from aider.z.banner import render_startup_banner

            version = ""
            model_line = ""
            status_lines = []
            if lines:
                # First line is "Z v..."; rest are status
                first = lines[0]
                if first.startswith("Z "):
                    version = first[2:].strip()
                else:
                    status_lines.append(first)
                if len(lines) > 1:
                    model_line = lines[1]
                    status_lines.extend(lines[2:])
            render_startup_banner(
                self.io.console,
                version=version,
                model_line=model_line,
                status_lines=status_lines,
                pretty=True,
            )
            return

        bold = True
        for line in lines:
            self.io.tool_output(line, bold=bold)
            bold = False

    def _record_inspect_path(self, path: str, *, via: str = "read") -> None:
        """Session evidence that a named file/area was actually opened."""
        try:
            eng = getattr(self, "uncertainty_engine", None)
            if eng is None or not hasattr(eng, "record_execution"):
                return
            try:
                rel = self.get_rel_fname(path)
            except Exception:
                rel = str(path)
            eng.record_execution(f"inspect: {via} {rel}")
        except Exception:
            pass

    def add_rel_fname(self, rel_fname):
        self.abs_fnames.add(self.abs_root_path(rel_fname))
        self._record_inspect_path(rel_fname, via="read")
        self.check_added_files()

    def drop_rel_fname(self, fname):
        abs_fname = self.abs_root_path(fname)
        if abs_fname in self.abs_fnames:
            self.abs_fnames.remove(abs_fname)
            return True

    def abs_root_path(self, path):
        key = path
        if key in self.abs_root_path_cache:
            return self.abs_root_path_cache[key]

        # Never let an absolute path escape the project root (pathlib would
        # otherwise ignore self.root when `path` is absolute).
        under = self.path_under_root(path)
        if under is not None:
            self.abs_root_path_cache[key] = under
            return under

        # Escape attempt / unresolvable — keep result under root by basename
        root = Path(utils.safe_abs_path(self.root))
        res = utils.safe_abs_path(root / Path(path).name)
        self.abs_root_path_cache[key] = res
        return res

    def path_under_root(self, path):
        """Return resolved absolute path if it lies under coder.root, else None."""
        try:
            root = Path(utils.safe_abs_path(self.root))
        except (OSError, RuntimeError, TypeError, ValueError):
            return None
        try:
            candidate = Path(path)
            if not candidate.is_absolute():
                candidate = root / candidate
            resolved = Path(utils.safe_abs_path(candidate))
            resolved.relative_to(root)
            return str(resolved)
        except (ValueError, OSError, RuntimeError, TypeError):
            return None

    def _blocks_dependency_fabrication(self, path, full_path=None) -> bool:
        """
        True if we refused to create this path because it would shadow a real dependency.

        Uses declared manifests + session ModuleNotFoundError / failed-install names.
        """
        try:
            from aider.z.deps import (
                collect_declared_dependencies,
                extract_missing_modules,
                extract_pip_install_targets,
                is_dependency_fabrication,
            )
        except Exception:
            return False

        root = Path(getattr(self, "root", None) or Path.cwd())
        try:
            rel = self.get_rel_fname(full_path or path)
        except Exception:
            rel = str(path)

        missing: set[str] = set()
        # Session execution / verify log
        try:
            eng = getattr(self, "uncertainty_engine", None)
            log = ""
            if eng is not None and getattr(eng, "ctx", None) is not None:
                log = getattr(eng.ctx, "execution_log", "") or ""
                ver = getattr(eng.ctx, "last_verification", None)
                if ver is not None:
                    log += "\n" + (getattr(ver, "output_excerpt", "") or "")
                    log += "\n" + (getattr(ver, "error", "") or "")
                    log += "\n" + (getattr(ver, "smoke_detail", "") or "")
            missing |= extract_missing_modules(log)
            missing |= extract_pip_install_targets(log)
        except Exception:
            pass
        # Also scan recent chat for ModuleNotFoundError
        try:
            for msg in (getattr(self, "done_messages", None) or [])[-8:]:
                content = msg.get("content") if isinstance(msg, dict) else None
                if isinstance(content, str):
                    missing |= extract_missing_modules(content)
        except Exception:
            pass

        try:
            declared = collect_declared_dependencies(root)
        except Exception:
            declared = set()

        reason = is_dependency_fabrication(
            rel,
            root=root,
            declared=declared,
            missing_modules=missing,
        )
        if not reason:
            return False

        self.io.tool_error(f"Blocked dependency fabrication: {rel}")
        self.io.tool_error(reason)
        self.io.tool_output(
            "Install the real package (e.g. from the project's requirements) "
            "or stop and ask a human — do not create a local stand-in."
        )
        # Record for uncertainty detector / gate
        try:
            eng = getattr(self, "uncertainty_engine", None)
            if eng is not None and hasattr(eng, "record_execution"):
                eng.record_execution(
                    f"BLOCKED dependency fabrication: {rel} ({reason})"
                )
            blocked = getattr(self, "_z_blocked_dep_fabrication", None)
            if blocked is None:
                self._z_blocked_dep_fabrication = []
                blocked = self._z_blocked_dep_fabrication
            blocked.append({"path": rel, "reason": reason})
        except Exception:
            pass
        return True

    fences = all_fences
    fence = fences[0]

    def show_pretty(self):
        if not self.pretty:
            return False

        # only show pretty output if fences are the normal triple-backtick
        if self.fence[0][0] != "`":
            return False

        return True

    def _stop_waiting_spinner(self):
        """Clear the waiting spinner."""
        spinner = getattr(self, "waiting_spinner", None)
        try:
            if getattr(self, "io", None) is not None:
                self.io.agent_busy = False
        except Exception:
            pass
        if not spinner:
            return
        try:
            spinner.stop()
        finally:
            self.waiting_spinner = None

    def _phase_spinner_start(self, text: str) -> None:
        """Start (or restart) the mascot/eyes spinner for a planning step."""
        self._stop_waiting_spinner()
        if not text:
            return
        # Make busy state unmistakable: leave prompt_toolkit chrome, announce interrupt.
        label = text
        if "Ctrl+C" not in label:
            label = f"{text}  · Ctrl+C to interrupt"
        try:
            if not self.show_pretty():
                # Non-pretty / dumb terminals: still print a static breadcrumb
                self.io.tool_output(label)
                return
        except Exception:
            return
        try:
            # Separate from leftover slash-menu / prompt redraw so \r spinner
            # does not overwrite file paths or completer chrome.
            import sys as _sys

            _sys.stdout.write("\n")
            _sys.stdout.flush()
        except Exception:
            pass
        try:
            if getattr(self.io, "z_theme", True):
                self.waiting_spinner = waiting_display(label)
            else:
                self.waiting_spinner = WaitingSpinner(label)
            self.waiting_spinner.start()
            try:
                self.io.agent_busy = True
                self.io._stop_agent_busy = self._phase_spinner_stop
            except Exception:
                pass
        except Exception:
            self.waiting_spinner = None
            try:
                self.io.tool_output(label)
            except Exception:
                pass

    def _phase_spinner_update(self, text: str) -> None:
        """Update spinner status text without stopping the eyes animation."""
        if not text:
            return
        label = text
        if "Ctrl+C" not in label:
            label = f"{text}  · Ctrl+C to interrupt"
        spinner = getattr(self, "waiting_spinner", None)
        if spinner is None:
            self._phase_spinner_start(text)
            return
        try:
            if hasattr(spinner, "set_text"):
                spinner.set_text(label)
            elif hasattr(spinner, "text"):
                spinner.text = label
            elif hasattr(spinner, "spinner") and hasattr(spinner.spinner, "text"):
                spinner.spinner.text = label
        except Exception:
            pass

    def _phase_spinner_stop(self) -> None:
        """Stop planning-phase spinner before printing or prompting."""
        self._stop_waiting_spinner()

    def get_abs_fnames_content(self):
        for fname in list(self.abs_fnames):
            content = self.io.read_text(fname)

            if content is None:
                relative_fname = self.get_rel_fname(fname)
                self.io.tool_warning(f"Dropping {relative_fname} from the chat.")
                self.abs_fnames.remove(fname)
            else:
                yield fname, content

    def choose_fence(self):
        all_content = ""
        for _fname, content in self.get_abs_fnames_content():
            all_content += content + "\n"
        for _fname in self.abs_read_only_fnames:
            content = self.io.read_text(_fname)
            if content is not None:
                all_content += content + "\n"

        lines = all_content.splitlines()
        good = False
        for fence_open, fence_close in self.fences:
            if any(line.startswith(fence_open) or line.startswith(fence_close) for line in lines):
                continue
            good = True
            break

        if good:
            self.fence = (fence_open, fence_close)
        else:
            self.fence = self.fences[0]
            self.io.tool_warning(
                "Unable to find a fencing strategy! Falling back to:"
                f" {self.fence[0]}...{self.fence[1]}"
            )

        return

    def get_files_content(self, fnames=None):
        if not fnames:
            fnames = self.abs_fnames

        prompt = ""
        for fname, content in self.get_abs_fnames_content():
            if not is_image_file(fname):
                relative_fname = self.get_rel_fname(fname)
                prompt += "\n"
                prompt += relative_fname
                prompt += f"\n{self.fence[0]}\n"

                prompt += content

                # lines = content.splitlines(keepends=True)
                # lines = [f"{i+1:03}:{line}" for i, line in enumerate(lines)]
                # prompt += "".join(lines)

                prompt += f"{self.fence[1]}\n"

        return prompt

    def get_read_only_files_content(self):
        prompt = ""
        for fname in self.abs_read_only_fnames:
            content = self.io.read_text(fname)
            if content is not None and not is_image_file(fname):
                relative_fname = self.get_rel_fname(fname)
                prompt += "\n"
                prompt += relative_fname
                prompt += f"\n{self.fence[0]}\n"
                prompt += content
                prompt += f"{self.fence[1]}\n"
        return prompt

    def get_cur_message_text(self):
        text = ""
        for msg in self.cur_messages:
            text += msg["content"] + "\n"
        return text

    def get_ident_mentions(self, text):
        # Split the string on any character that is not alphanumeric
        # \W+ matches one or more non-word characters (equivalent to [^a-zA-Z0-9_]+)
        words = set(re.split(r"\W+", text))
        return words

    def get_ident_filename_matches(self, idents):
        all_fnames = defaultdict(set)
        for fname in self.get_all_relative_files():
            # Skip empty paths or just '.'
            if not fname or fname == ".":
                continue

            try:
                # Handle dotfiles properly
                path = Path(fname)
                base = path.stem.lower()  # Use stem instead of with_suffix("").name
                if len(base) >= 5:
                    all_fnames[base].add(fname)
            except ValueError:
                # Skip paths that can't be processed
                continue

        matches = set()
        for ident in idents:
            if len(ident) < 5:
                continue
            matches.update(all_fnames[ident.lower()])

        return matches

    def get_repo_map(self, force_refresh=False):
        if not self.repo_map:
            return

        cur_msg_text = self.get_cur_message_text()
        mentioned_fnames = self.get_file_mentions(cur_msg_text)
        mentioned_idents = self.get_ident_mentions(cur_msg_text)

        mentioned_fnames.update(self.get_ident_filename_matches(mentioned_idents))

        all_abs_files = set(self.get_all_abs_files())
        repo_abs_read_only_fnames = set(self.abs_read_only_fnames) & all_abs_files
        chat_files = set(self.abs_fnames) | repo_abs_read_only_fnames
        other_files = all_abs_files - chat_files

        repo_content = self.repo_map.get_repo_map(
            chat_files,
            other_files,
            mentioned_fnames=mentioned_fnames,
            mentioned_idents=mentioned_idents,
            force_refresh=force_refresh,
        )

        # fall back to global repo map if files in chat are disjoint from rest of repo
        if not repo_content:
            repo_content = self.repo_map.get_repo_map(
                set(),
                all_abs_files,
                mentioned_fnames=mentioned_fnames,
                mentioned_idents=mentioned_idents,
            )

        # fall back to completely unhinted repo
        if not repo_content:
            repo_content = self.repo_map.get_repo_map(
                set(),
                all_abs_files,
            )

        return repo_content

    def get_repo_messages(self):
        repo_messages = []
        repo_content = self.get_repo_map()
        if repo_content:
            repo_messages += [
                dict(role="user", content=repo_content),
                dict(
                    role="assistant",
                    content="Ok, I won't try and edit those files without asking first.",
                ),
            ]
        return repo_messages

    def get_readonly_files_messages(self):
        readonly_messages = []

        # Handle non-image files
        read_only_content = self.get_read_only_files_content()
        if read_only_content:
            readonly_messages += [
                dict(
                    role="user", content=self.gpt_prompts.read_only_files_prefix + read_only_content
                ),
                dict(
                    role="assistant",
                    content="Ok, I will use these files as references.",
                ),
            ]

        # Handle image files
        images_message = self.get_images_message(self.abs_read_only_fnames)
        if images_message is not None:
            readonly_messages += [
                images_message,
                dict(role="assistant", content="Ok, I will use these images as references."),
            ]

        return readonly_messages

    def get_chat_files_messages(self):
        chat_files_messages = []
        if self.abs_fnames:
            files_content = self.gpt_prompts.files_content_prefix
            files_content += self.get_files_content()
            files_reply = self.gpt_prompts.files_content_assistant_reply
        elif self.get_repo_map() and self.gpt_prompts.files_no_full_files_with_repo_map:
            files_content = self.gpt_prompts.files_no_full_files_with_repo_map
            files_reply = self.gpt_prompts.files_no_full_files_with_repo_map_reply
        else:
            files_content = self.gpt_prompts.files_no_full_files
            files_reply = "Ok."

        if files_content:
            chat_files_messages += [
                dict(role="user", content=files_content),
                dict(role="assistant", content=files_reply),
            ]

        images_message = self.get_images_message(self.abs_fnames)
        if images_message is not None:
            chat_files_messages += [
                images_message,
                dict(role="assistant", content="Ok."),
            ]

        return chat_files_messages

    def get_images_message(self, fnames):
        supports_images = self.main_model.info.get("supports_vision")
        supports_pdfs = self.main_model.info.get("supports_pdf_input") or self.main_model.info.get(
            "max_pdf_size_mb"
        )

        # https://github.com/BerriAI/litellm/pull/6928
        supports_pdfs = supports_pdfs or "claude-3-5-sonnet-20241022" in self.main_model.name

        if not (supports_images or supports_pdfs):
            return None

        image_messages = []
        for fname in fnames:
            if not is_image_file(fname):
                continue

            mime_type, _ = mimetypes.guess_type(fname)
            if not mime_type:
                continue

            with open(fname, "rb") as image_file:
                encoded_string = base64.b64encode(image_file.read()).decode("utf-8")
            image_url = f"data:{mime_type};base64,{encoded_string}"
            rel_fname = self.get_rel_fname(fname)

            if mime_type.startswith("image/") and supports_images:
                image_messages += [
                    {"type": "text", "text": f"Image file: {rel_fname}"},
                    {"type": "image_url", "image_url": {"url": image_url, "detail": "high"}},
                ]
            elif mime_type == "application/pdf" and supports_pdfs:
                image_messages += [
                    {"type": "text", "text": f"PDF file: {rel_fname}"},
                    {"type": "image_url", "image_url": image_url},
                ]

        if not image_messages:
            return None

        return {"role": "user", "content": image_messages}

    def run_stream(self, user_message):
        self.io.user_input(user_message)
        self.init_before_message()
        yield from self.send_message(user_message)

    def init_before_message(self):
        self.aider_edited_files = set()
        self.reflected_message = None
        self.num_reflections = 0
        self.lint_outcome = None
        self.test_outcome = None
        self.shell_commands = []
        self.message_cost = 0
        # Per-turn reflection log for drift detection (ask cooldown is per-task)
        self._drift_reflection_log = []
        # NI auto-seed fires at most once per user turn
        self._z_ni_auto_seed_done = False
        self._z_ni_user_message = None

        if self.repo:
            self.commit_before_message.append(self.repo.get_head_commit_sha())

    def run(self, with_message=None, preproc=True):
        try:
            if with_message:
                self.io.user_input(with_message)
                self.run_one(with_message, preproc)
                return self.partial_response_content
            while True:
                try:
                    if not self.io.placeholder:
                        self.copy_context()
                    user_message = self.get_input()
                    self.run_one(user_message, preproc)
                    self.show_undo_hint()
                except KeyboardInterrupt:
                    self.keyboard_interrupt()
        except EOFError:
            return

    def copy_context(self):
        if self.auto_copy_context:
            self.commands.cmd_copy_context()

    def get_input(self):
        # Never leave planning/LLM spinner running into the next prompt.
        self._phase_spinner_stop()
        inchat_files = self.get_inchat_relative_files()
        read_only_files = [self.get_rel_fname(fname) for fname in self.abs_read_only_fnames]
        all_files = sorted(set(inchat_files + read_only_files))
        edit_format = "" if self.edit_format == self.main_model.edit_format else self.edit_format
        from aider.z.ux_prompt import resolve_prompt_chrome

        prompt_chrome = resolve_prompt_chrome(
            forced_task_mode=getattr(self, "forced_task_mode", None),
            edit_format=self.edit_format,
            default_edit_format=getattr(self.main_model, "edit_format", None),
            multiline=bool(getattr(self.io, "multiline_mode", False)),
        )
        return self.io.get_input(
            self.root,
            all_files,
            self.get_addable_relative_files(),
            self.commands,
            self.abs_read_only_fnames,
            edit_format=edit_format,
            prompt_chrome=prompt_chrome,
        )

    def preproc_user_input(self, inp):
        if not inp:
            return

        if self.commands.is_command(inp):
            return self.commands.run(inp)

        self.check_for_file_mentions(inp)
        inp = self.check_for_urls(inp)

        return inp

    def run_one(self, user_message, preproc):
        self.init_before_message()
        if isinstance(user_message, str):
            self._z_ni_user_message = user_message

        if preproc:
            message = self.preproc_user_input(user_message)
        else:
            message = user_message

        # Start-of-task: mode + intent, then gated pipelines (P0.1 / P0.2)
        if message and not (isinstance(message, str) and message.startswith("/")):
            user_text = user_message if isinstance(user_message, str) else message
            mode, intent = self._resolve_task_mode_and_intent(user_text)
            self.task_mode = mode
            self.task_intent = intent
            eng0 = getattr(self, "uncertainty_engine", None)
            if eng0 is not None and getattr(eng0, "ctx", None) is not None:
                eng0.ctx.task_intent = intent
                eng0.ctx.task_mode = mode.value if hasattr(mode, "value") else str(mode)

            # Skills + overlapped explore, with continuous mascot/eyes updates.
            # Explore forks off the critical path while checklist/plan draft (T3).
            from aider.z.ux_preamble import TurnPreamble, ux_verbose

            self._turn_preamble = TurnPreamble(verbose=ux_verbose(coder=self))
            try:
                self._phase_spinner_start("Planning — matching skills…")
                self._maybe_pull_skills(user_text, checkpoint="turn")

                if mode.allows_explore_pass:
                    try:
                        from aider.z.explore import explore_pass_enabled

                        if explore_pass_enabled():
                            self._phase_spinner_start(
                                "Planning — exploring related files…"
                            )
                    except Exception:
                        pass
                explore_fut = self._start_explore_pass_async(user_text)

                # House instructions (AGENTS.md) — once per session
                self._maybe_inject_house_instructions()

                if mode.allows_requirement_decomposition:
                    self._phase_spinner_start(
                        "Planning — drafting approach checklist…"
                    )
                    self._maybe_begin_uncertainty_task(user_text)

                # PLAN mode: inject reminder; skip high-stakes *implement* plan confirm
                from aider.z.task_mode import TaskMode as _TM

                if mode is _TM.PLAN:
                    self._phase_spinner_start("Planning — refining plan interview…")
                    self._maybe_advance_plan_interview(user_text)
                    self._phase_spinner_stop()
                    self._inject_plan_mode_reminder()
                elif mode.allows_planning:
                    self._phase_spinner_start(
                        "Planning — drafting implementation plan…"
                    )
                    if not self._maybe_require_implementation_plan(user_text):
                        self._cancel_explore_pass(explore_fut)
                        return
                else:
                    # Ensure no stale plan from a prior turn leaks into this message
                    eng = getattr(self, "uncertainty_engine", None)
                    if eng is not None and getattr(eng, "ctx", None) is not None:
                        eng.ctx.plan = None
                        eng.ctx.plan_required = False
                        eng.ctx.plan_approved = True
            finally:
                self._phase_spinner_stop()

            # Join explore before the model turn so findings still land in context
            self._finish_explore_pass(explore_fut)
            try:
                pre = getattr(self, "_turn_preamble", None)
                if pre is not None:
                    pre.flush(self.io)
            except Exception:
                pass

        while message:
            self.reflected_message = None
            # Track per-reflection edits + checklist movement for drift detection.
            was_reflection = int(getattr(self, "num_reflections", 0) or 0) >= 1
            files_before = set(self.aider_edited_files or ())
            status_before = {}
            evidence_before: dict = {}
            try:
                from aider.z.uncertainty.drift import evidence_snapshot, status_snapshot

                eng = getattr(self, "uncertainty_engine", None)
                cl = getattr(getattr(eng, "ctx", None), "checklist", None)
                status_before = status_snapshot(cl)
                evidence_before = evidence_snapshot(cl)
                if self._drift_debug_enabled() and cl is not None:
                    parts = []
                    for item in cl.items or []:
                        ev = getattr(item, "last_evidence", None)
                        n_files = len(getattr(ev, "file_hits", None) or []) if ev else 0
                        n_syms = len(getattr(ev, "symbol_hits", None) or []) if ev else 0
                        parts.append(
                            f"{item.id[:8]}:{item.status}:ev={n_files + n_syms}"
                        )
                    self._drift_debug(
                        f"turn-start n_reflections={self.num_reflections} "
                        f"items=[{', '.join(parts) or 'none'}]"
                    )
            except Exception:
                status_before = {}
                evidence_before = {}

            list(self.send_message(message))

            # Lightweight checklist rescore for drift — independent of the
            # clean-exit-only full uncertainty pipeline. Without this,
            # multi-reflection sessions never update last_evidence/status.
            try:
                self._rescore_checklist_for_drift()
            except Exception:
                pass

            # Cheap deterministic detectors (absorption / siblings / established
            # solutions) — same reflection-safe pattern. Full analyze_edits
            # (incl. model-backed edge cases) still runs only on clean exit.
            try:
                self._run_cheap_detectors_for_reflection()
            except Exception:
                pass

            try:
                self._record_drift_reflection_turn(
                    was_reflection=was_reflection,
                    files_before=files_before,
                    status_before=status_before,
                    evidence_before=evidence_before,
                )
            except Exception:
                pass

            # Soft stop: model claimed done while High nodes / verify still bad
            if not self.reflected_message:
                try:
                    self._maybe_soft_stop_done_claim()
                except Exception:
                    pass

            if not self.reflected_message:
                break

            # Drift BEFORE exhaustion — the reflection that would otherwise
            # trip the cap gets one last chance to redirect/stop first.
            # Recording already happened above; this only evaluates the decision.
            if isinstance(self.reflected_message, str) and not self.reflected_message.startswith(
                "/"
            ):
                try:
                    drift_result = self._maybe_detect_drift()
                    if drift_result is not None:
                        if getattr(drift_result, "stop", False):
                            self.reflected_message = None
                            break
                        if drift_result.refocus_message:
                            self.reflected_message = drift_result.refocus_message
                            self.num_reflections += 1
                            message = self.reflected_message
                            # Continue as a normal reflection, not exhaustion
                            continue
                except Exception:
                    pass

            if self.num_reflections >= self.max_reflections:
                # Control-flow fix: do not silently drop a still-failing auto-fix
                # loop — raise the same commit-blocked / uncertainty reporting
                # every other stop path already uses (logveil IPv4 re-run).
                pending = self.reflected_message
                try:
                    from aider.z.uncertainty.gate import report_auto_fix_exhaustion

                    report_auto_fix_exhaustion(
                        self,
                        max_reflections=self.max_reflections,
                        pending_reflect=pending if isinstance(pending, str) else "",
                    )
                except Exception:
                    self.io.tool_warning(
                        f"Only {self.max_reflections} reflections allowed, stopping."
                    )
                    self.io.tool_error(
                        "Commit blocked: reflection loop exhausted with pending "
                        "work still unresolved. A human needs to look."
                    )
                self.reflected_message = None
                # Exhaustion returns before the clean-exit capture site below.
                # If a commit already landed earlier this session, that verified
                # work is still worth capturing (React: real fix committed, then
                # an unrelated tangent burned the reflection budget).
                if (
                    isinstance(user_message, str)
                    and not user_message.startswith("/")
                    and getattr(self, "last_aider_commit_hash", None)
                    and self.aider_edited_files
                ):
                    try:
                        # Session-scoped diff: mid-turn commits + trailing dirty
                        # edit, not just the uncommitted remainder.
                        self._maybe_suggest_skill(
                            user_message, session_scoped_diff=True
                        )
                    except Exception:
                        pass
                return

            self.num_reflections += 1
            message = self.reflected_message
            # Mid-workflow checkpoint: re-route skills for the *next* step
            # (e.g. after verify gate asks for tests, or a requirement gap).
            # Injects newly relevant skills; skips scaffolds already satisfied.
            if isinstance(message, str) and not message.startswith("/"):
                self._maybe_pull_skills(message, checkpoint="reflect")
                try:
                    from aider.z.skills.session import note_scaffold_progress

                    note_scaffold_progress(root=getattr(self, "root", None))
                except Exception:
                    pass

        # After a non-trivial completed edit turn, offer to save a reusable skill
        if (
            isinstance(user_message, str)
            and not user_message.startswith("/")
            and self.aider_edited_files
            and not self.reflected_message
        ):
            try:
                from aider.z.skills.session import note_scaffold_progress

                note_scaffold_progress(root=getattr(self, "root", None))
            except Exception:
                pass
            self._maybe_suggest_skill(user_message)

    def _resolve_task_mode_and_intent(self, user_message: str):
        """Per-message TaskMode + TaskIntent (P0.1 / P0.2)."""
        from aider.z.task_mode import TaskMode, classify_task_mode
        from aider.z.uncertainty.intent import extract_intent

        # Explicit /ask or /context — hard mapping (not sticky across turns)
        # Explicit TaskMode from /plan|/ask commands wins over heuristics.
        forced_mode_str = None
        explicit = getattr(self, "forced_task_mode", None)
        if isinstance(explicit, TaskMode):
            forced_mode_str = explicit.value
        elif self.edit_format in ("ask", "context"):
            forced_mode_str = "ask"

        recent = []
        try:
            for msg in (self.done_messages or [])[-6:]:
                if isinstance(msg, dict) and msg.get("role") == "user":
                    content = msg.get("content") or ""
                    if isinstance(content, str) and content.strip():
                        recent.append(content.strip()[:500])
        except Exception:
            recent = []

        intent = extract_intent(
            user_message or "",
            recent_messages=recent,
            forced_mode=forced_mode_str,
        )
        if isinstance(explicit, TaskMode):
            intent.mode = explicit.value
            return explicit, intent

        mode = classify_task_mode(
            self.edit_format,
            user_message or "",
            intent_mode=intent.mode,
        )
        # Explicit /ask|/context always stay non-edit modes
        if self.edit_format in ("ask", "context") and mode is TaskMode.IMPLEMENT:
            mode = (
                TaskMode.INVESTIGATE
                if intent.mode == "investigate"
                else TaskMode.ASK
            )
            intent.mode = mode.value
        return mode, intent

    def _maybe_pull_skills(self, user_message: str, *, checkpoint: str = "turn"):
        """
        Skill-router checkpoint: retrieve candidates, route apply/skip, inject
        only skills needed for *this* workflow step.

        Also builds a capability plan above named skills — gaps are compensated
        with explicit workflows even when no skill matches.

        Called at turn start and again on each reflection so skills can be
        injected progressively (not one-and-done).
        """
        if not user_message or len(user_message.strip()) < 12:
            return
        try:
            from aider.z.skills.session import (
                format_skills_for_context,
                get_session_skill_index,
                load_skills_for_session,
                pull_skills_for_checkpoint,
            )
            from aider.z.task_mode import TaskMode
            from aider.z.control_plane_budget import (
                capability_plan_fingerprint,
                control_plane_compact_enabled,
                format_capability_directive,
            )
            from aider.z.uncertainty.capabilities import (
                build_capability_plan,
                format_capability_plan,
            )

            if not get_session_skill_index():
                load_skills_for_session(io=None)

            self._phase_spinner_update("Planning — routing skills…")
            skills, skip_reasons = pull_skills_for_checkpoint(
                user_message,
                root=getattr(self, "root", None),
                limit=2,
                checkpoint=checkpoint,
            )
            self._phase_spinner_update("Planning — building capability plan…")
            # Retrieve trace: verbose, --yes-always / NI, or Z_SKILL_RETRIEVE_LOG
            try:
                import os

                from aider.z.skills.near_dup import get_last_retrieve_trace

                log_retrieve = bool(getattr(self, "verbose", False))
                if getattr(self.io, "yes", None) is True:
                    log_retrieve = True
                if os.environ.get("Z_SKILL_RETRIEVE_LOG", "").strip().lower() in (
                    "1",
                    "true",
                    "yes",
                ):
                    log_retrieve = True
                if log_retrieve:
                    tr = get_last_retrieve_trace()
                    if tr is not None:
                        for line in tr.format_lines():
                            self.io.tool_output(line)
            except Exception:
                pass
            if getattr(self, "verbose", False) and skip_reasons:
                for reason in skip_reasons[:6]:
                    self.io.tool_output(f"Skill skip — {reason}")
            elif skip_reasons and getattr(self.io, "yes", None) is True:
                for reason in skip_reasons[:4]:
                    self.io.tool_output(f"Skill skip — {reason}")

            skill_caps = [s.capability for s in (skills or []) if getattr(s, "capability", None)]
            skill_ids = [s.id for s in (skills or []) if getattr(s, "id", None)]
            mode = getattr(self, "task_mode", None)
            intent = getattr(self, "task_intent", None)
            if mode is not None and not getattr(mode, "allows_capability_inference", True):
                # ASK/INVESTIGATE/… — skills may still inject; skip capability plan
                cap_plan = build_capability_plan(intent=None, skill_capabilities=[], skill_ids=[])
                cap_plan.required = []
                cap_plan.coverage_gaps = []
                cap_plan.compensation = []
            else:
                cap_plan = build_capability_plan(
                    intent=intent,
                    skill_capabilities=skill_caps,
                    skill_ids=skill_ids,
                )
            eng = getattr(self, "uncertainty_engine", None)
            if eng is not None:
                eng.ctx.capability_plan = cap_plan
                eng.ctx._skill_capabilities = skill_caps
                eng.ctx._skill_ids = skill_ids

            blocks = []
            if skills:
                skill_block = format_skills_for_context(skills, checkpoint=checkpoint)
                if skill_block:
                    blocks.append(skill_block)
            # Surface capability gaps when specialized verification is needed.
            # Compact mode: thin directive; skip re-inject when fingerprint unchanged.
            if cap_plan.required and (
                cap_plan.coverage_gaps or checkpoint == "turn"
            ):
                fp = capability_plan_fingerprint(cap_plan)
                prev_fp = getattr(self, "_capability_plan_fingerprint", None)
                if control_plane_compact_enabled() and fp and fp == prev_fp:
                    pass  # already injected this gap set
                else:
                    if control_plane_compact_enabled():
                        cap_block = format_capability_directive(cap_plan)
                    else:
                        cap_block = format_capability_plan(cap_plan)
                    if cap_block:
                        blocks.append(cap_block)
                        self._capability_plan_fingerprint = fp

            if not blocks:
                return

            # Clear eyes spinner before printing results into the scrollback
            self._phase_spinner_stop()

            block = "\n\n".join(blocks)
            names = ", ".join(s.title for s in skills) if skills else "(capability plan only)"
            has_bug = any((s.kind or "") == "bug_pattern" for s in (skills or []))
            if has_bug and skills and all((s.kind or "") == "bug_pattern" for s in skills):
                label = "Bug-pattern hypothesis"
            elif checkpoint == "turn":
                label = "Applying skill(s)" if skills else "Capability plan"
            else:
                label = "Injecting skill(s) for this step" if skills else "Capability plan"
            why = "; ".join(
                f"{s.title} [{s.kind or 'playbook'}"
                + (f"/{','.join(s.languages)}" if s.languages else "")
                + "]"
                for s in (skills or [])
            ) or "capability coverage (skills + native abilities)"

            from aider.z.ux_preamble import ux_verbose

            quiet = not ux_verbose(coder=self)
            pre = getattr(self, "_turn_preamble", None)
            if pre is not None:
                pre.note_skills(
                    [s.title for s in (skills or []) if getattr(s, "title", None)],
                    capability_only=not bool(skills),
                )
                if cap_plan.coverage_gaps:
                    pre.note_gaps(len(cap_plan.coverage_gaps))

            if not quiet:
                self.io.tool_output(f"{label}: {names}")
                if getattr(self, "verbose", False):
                    self.io.tool_output(f"  why: {why}")
            if cap_plan.coverage_gaps:
                self.io.tool_warning(
                    f"Capability gaps ({len(cap_plan.coverage_gaps)}): "
                    "compensate with workflow — no skill ≠ skip verification."
                )
                # Gaps are informational — never a turn abort.
                if not skills and not quiet:
                    self.io.tool_output(
                        "No matching skill — continuing with native plan / "
                        "verify workflow (not stopped)."
                    )
            self.cur_messages += [
                {"role": "user", "content": block},
                {
                    "role": "assistant",
                    "content": (
                        "I'll follow the matched skills where relevant and "
                        "explicitly compensate for any capability coverage gaps "
                        "before claiming completion."
                    ),
                },
            ]
        except Exception as err:
            if getattr(self, "verbose", False):
                self.io.tool_warning(f"Skill/capability pull skipped: {err}")

    def _maybe_explore_pass(self, user_message: str) -> None:
        """Inject compact read-only findings when the chat is thin (sync)."""
        block = self._compute_explore_block(user_message)
        self._inject_explore_block(block)

    def _start_explore_pass_async(self, user_message: str):
        """
        Start explore in the background so checklist/plan can run in parallel.

        Returns a Future[str|None] or None when overlap is disabled / skipped.
        """
        try:
            from aider.z.latency import latency_overlap_enabled, submit_background

            if not latency_overlap_enabled():
                self._maybe_explore_pass(user_message)
                return None
            # Quick eligibility checks on the main thread (cheap)
            if not self._explore_pass_eligible(user_message):
                return None
            from aider.z.ux_preamble import ux_verbose

            if ux_verbose(coder=self):
                self.io.tool_output("Exploring related files (background)…")
            return submit_background(self._compute_explore_block, user_message)
        except Exception:
            self._maybe_explore_pass(user_message)
            return None

    def _finish_explore_pass(self, fut) -> None:
        """Join background explore and inject findings before the model turn."""
        if fut is None:
            return
        try:
            from aider.z.latency import join_future

            block = join_future(fut, timeout=12.0)
            self._inject_explore_block(block)
        except Exception:
            pass

    def _cancel_explore_pass(self, fut) -> None:
        if fut is None:
            return
        try:
            fut.cancel()
        except Exception:
            pass

    def _explore_pass_eligible(self, user_message: str) -> bool:
        if not user_message or len(user_message.strip()) < 8:
            return False
        try:
            from aider.z.explore import explore_pass_enabled
            from aider.z.task_mode import TaskMode

            if not explore_pass_enabled():
                return False
            mode = getattr(self, "task_mode", None)
            if mode is not None and not getattr(mode, "allows_explore_pass", False):
                return False
            if mode is TaskMode.ASK:
                return False
            if len(getattr(self, "abs_fnames", None) or []) >= 3:
                return False
            return True
        except Exception:
            return False

    def _compute_explore_block(self, user_message: str) -> str:
        """Pure compute for explore — safe to run on a worker thread."""
        if not self._explore_pass_eligible(user_message):
            return ""
        try:
            from aider.z.explore import run_explore_pass

            already = []
            try:
                already = list(self.get_inchat_relative_files() or [])
            except Exception:
                already = []
            return (
                run_explore_pass(
                    user_message,
                    root=getattr(self, "root", None) or ".",
                    already_in_chat=already,
                )
                or ""
            )
        except Exception:
            return ""

    def _inject_explore_block(self, block: str) -> None:
        if not block:
            return
        deep = "Explore scout" in block or "deep)" in block[:80]
        n_files = 0
        try:
            n_files = sum(
                1
                for line in block.splitlines()
                if line.startswith("### `") or line.startswith("- `")
            )
        except Exception:
            n_files = 0
        try:
            pre = getattr(self, "_turn_preamble", None)
            if pre is not None:
                pre.note_explore(n_files or 1)
        except Exception:
            pass
        from aider.z.ux_preamble import ux_verbose

        if ux_verbose(coder=self):
            try:
                self.io.tool_output(
                    "Explore scout: candidate files + signatures (read-only)."
                    if deep
                    else "Explore pass: candidate files found (read-only)."
                )
            except Exception:
                pass
        self.cur_messages += [
            {"role": "user", "content": block},
            {
                "role": "assistant",
                "content": (
                    "I'll use those explore findings as investigation targets "
                    "and ask to /add files before editing them."
                ),
            },
        ]

    def _maybe_inject_house_instructions(self) -> None:
        if getattr(self, "_house_instructions_injected", False):
            return
        try:
            from aider.z.house_instructions import load_house_instructions

            block = load_house_instructions(getattr(self, "root", None) or ".")
            if not block:
                self._house_instructions_injected = True
                return
            self._house_instructions_injected = True
            self.cur_messages += [
                {"role": "user", "content": block},
                {
                    "role": "assistant",
                    "content": (
                        "I'll follow the house AGENTS.md instructions where they "
                        "apply to this task."
                    ),
                },
            ]
            self.io.tool_output("Loaded house instructions (AGENTS.md).")
        except Exception as err:
            if getattr(self, "verbose", False):
                self.io.tool_warning(f"House instructions skipped: {err}")

    def _inject_plan_mode_reminder(self) -> None:
        try:
            from pathlib import Path

            from aider.z.plan_interview import (
                PlanInterviewStage,
                detect_stage,
                format_interview_reminder,
                plan_interview_enabled,
            )
            from aider.z.plan_mode import format_plan_mode_reminder, new_plan_path

            if not getattr(self, "_active_plan_path", None):
                path = new_plan_path(stem="task")
                self._active_plan_path = str(path)
                if plan_interview_enabled():
                    self._plan_interview_stage = PlanInterviewStage.CLARIFY
                    self._plan_clarify_asked = False
            path = Path(self._active_plan_path)

            if plan_interview_enabled():
                stage = getattr(self, "_plan_interview_stage", None)
                detected = detect_stage(active_path=self._active_plan_path)
                if detected is PlanInterviewStage.READY:
                    stage = PlanInterviewStage.READY
                elif stage is None:
                    stage = detected
                self._plan_interview_stage = stage
                block = format_interview_reminder(stage, plan_path=path)
                if stage is PlanInterviewStage.CLARIFY:
                    self._plan_clarify_asked = True
            else:
                block = format_plan_mode_reminder(path)

            self.cur_messages += [
                {"role": "user", "content": block},
                {
                    "role": "assistant",
                    "content": (
                        f"Plan mode acknowledged. I'll only write the plan at `{path}` "
                        "and will not edit product code until /plan-exit."
                    ),
                },
            ]
            stage_s = getattr(getattr(self, "_plan_interview_stage", None), "value", None)
            extra = f" (interview: {stage_s})" if stage_s else ""
            self.io.tool_output(f"Plan mode — artifact path: {path}{extra}")
        except Exception as err:
            if getattr(self, "verbose", False):
                self.io.tool_warning(f"Plan mode reminder skipped: {err}")

    def _maybe_advance_plan_interview(self, user_text: str) -> None:
        """After clarify answers, move interview to draft on the next user turn."""
        try:
            from aider.z.plan_interview import (
                PlanInterviewStage,
                advance_after_user_reply,
                plan_interview_enabled,
            )
            from aider.z.task_mode import TaskMode

            if not plan_interview_enabled():
                return
            if getattr(self, "task_mode", None) is not TaskMode.PLAN:
                return
            if not user_text or user_text.strip().startswith("/"):
                return
            stage = getattr(self, "_plan_interview_stage", PlanInterviewStage.CLARIFY)
            if stage is PlanInterviewStage.CLARIFY and getattr(
                self, "_plan_clarify_asked", False
            ):
                self._plan_interview_stage = advance_after_user_reply(stage)
        except Exception:
            pass

    def _maybe_run_tool_loop(self, content: str) -> bool:
        """
        Run read-only z-tool fences from the model reply.
        Returns True if a reflect was scheduled (caller should skip apply).
        """
        try:
            from aider.z.tool_loop import run_tool_loop, tool_loop_enabled

            if not tool_loop_enabled() or not content:
                return False
            # Avoid infinite tool-loop reflections
            if int(getattr(self, "num_reflections", 0) or 0) >= 3:
                return False
            res = run_tool_loop(content, root=getattr(self, "root", None) or ".")
            if not res.ran:
                return False
            names = ", ".join(f"{c.name}" for c in res.calls)
            self.io.tool_output(f"Tool-loop: ran {len(res.calls)} read-only tool(s) ({names}).")
            prev = getattr(self, "reflected_message", None) or ""
            self.reflected_message = (
                (prev + "\n\n" + res.reflect_message).strip()
                if prev
                else res.reflect_message
            )
            return True
        except Exception as err:
            if getattr(self, "verbose", False):
                self.io.tool_warning(f"Tool-loop skipped: {err}")
            return False

    def _maybe_soft_stop_done_claim(self) -> None:
        from aider.z.uncertainty.done_gate import (
            count_open_high,
            looks_like_done_claim,
            soft_stop_reason,
        )

        content = getattr(self, "partial_response_content", None) or ""
        if not looks_like_done_claim(content):
            return
        store = getattr(self, "uncertainty_store", None)
        open_high = 0
        if store is not None:
            try:
                open_high = count_open_high(store.list(include_resolved=False))
            except Exception:
                open_high = 0
        eng = getattr(self, "uncertainty_engine", None)
        plan_pending = False
        completion_incomplete = False
        if eng is not None:
            try:
                plan_pending = bool(eng.edits_blocked_pending_plan())
            except Exception:
                plan_pending = False
            report = getattr(getattr(eng, "ctx", None), "completion_report", None)
            if report is not None and not getattr(report, "complete", True):
                completion_incomplete = True
        last_verify_failed = getattr(self, "test_outcome", None) is False
        reason = soft_stop_reason(
            open_high_count=open_high,
            last_verify_failed=bool(last_verify_failed),
            plan_pending=plan_pending,
            completion_incomplete=completion_incomplete,
        )
        if reason:
            self.io.tool_warning(reason)
            self.reflected_message = reason

    def _skill_capture_skip(self, reason: str) -> None:
        """Visible skip reason — silence here is how capture looked 'broken'."""
        if getattr(self, "verbose", False) or os.environ.get(
            "Z_SKILL_CAPTURE_LOG", ""
        ).strip().lower() in ("1", "true", "yes"):
            self.io.tool_output(f"Skill capture skipped: {reason}")

    def _drift_debug_enabled(self) -> bool:
        return os.environ.get("Z_DRIFT_DEBUG", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )

    def _drift_debug(self, msg: str) -> None:
        """Env-gated drift instrumentation — print only, never changes control flow."""
        if not self._drift_debug_enabled():
            return
        try:
            self.io.tool_output(f"[drift-debug] {msg}")
        except Exception:
            pass

    def _detector_debug_enabled(self) -> bool:
        from aider.z.uncertainty.detector_debug import detector_debug_enabled

        return detector_debug_enabled()

    def _detector_debug(self, msg: str) -> None:
        """Env-gated cheap-detector instrumentation — print only, no behavior change."""
        from aider.z.uncertainty.detector_debug import detector_debug

        detector_debug(msg, io=getattr(self, "io", None))

    def _session_start_commit(self) -> Optional[str]:
        """HEAD SHA recorded at the start of this run_one() (if any)."""
        before = getattr(self, "commit_before_message", None) or []
        if before:
            sha = before[-1]
            if sha:
                return str(sha)
        return None

    def _maybe_suggest_skill(self, user_message: str, *, session_scoped_diff: bool = False):
        """After a task: ask to create a skill grounded in the real diff/files.

        Fires after a clean verify *or* a human-approved / force / manual commit.
        Earlier gating required meaningful_pass + cleared hold only — so force/
        medium-ack completions (most real-repo runs) never offered capture.

        ``session_scoped_diff`` (exhaustion path): ground in the cumulative
        diff since this turn started (mid-turn commits + trailing dirty edit),
        not just ``git diff HEAD`` which misses already-committed work.
        """
        edited = self.aider_edited_files or set()
        if len(edited) < 1:
            self._skill_capture_skip("no edited files")
            return
        # Skip tiny one-liners / pure renames
        if len((user_message or "").strip()) < 24 and len(edited) < 2:
            self._skill_capture_skip("turn too small")
            return
        # Never block task completion on skill capture (evals / --yes)
        if os.environ.get("Z_SKIP_SKILL_CAPTURE", "").strip().lower() in (
            "1",
            "true",
            "yes",
        ):
            self._skill_capture_skip("Z_SKIP_SKILL_CAPTURE set")
            return

        # Classify before the yes-always guard: bug_pattern capture is already
        # gated by task_is_bugfix_intent and is the case where CI/--yes-always
        # runs need organizational memory most. Ordinary playbook suggestions
        # stay skipped under yes-always (noisy / multi-minute on every task).
        try:
            from aider.z.skills.router import task_is_bugfix_intent

            is_bugfix = task_is_bugfix_intent(user_message)
        except Exception:
            is_bugfix = False

        if getattr(self.io, "yes", None) is True and not is_bugfix:
            # yes-always would auto-accept and hang on multi-minute model calls
            self._skill_capture_skip("--yes / yes_always")
            return

        last_ver = getattr(self, "last_verification", None)
        committed = bool(getattr(self, "last_aider_commit_hash", None))
        verify_ok = bool(
            last_ver is not None and getattr(last_ver, "meaningful_pass", False)
        )
        # Offer when verify is green OR this turn produced a commit (including
        # human-approved force / medium-ack / /commit paths).
        if last_ver is not None and not verify_ok and not committed:
            self._skill_capture_skip("verify incomplete and no commit this turn")
            return
        if getattr(self, "_z_gate_hold_dirty", False) and not committed:
            self._skill_capture_skip("gate hold still set (no approved commit)")
            return
        try:
            auto_bug_capture = bool(
                is_bugfix and getattr(self.io, "yes", None) is True
            )
            if not auto_bug_capture:
                if not self.io.confirm_ask(
                    "Want me to save this as a reusable skill for next time?",
                    default="n",
                    explicit_yes_required=True,
                ):
                    self._skill_capture_skip("user declined")
                    return
            else:
                self.io.tool_output(
                    "Bug-fix detected under --yes-always — "
                    "auto-capturing bug_pattern skill…"
                )
            from aider.z.skills.cli import offer_view_new_skill, save_skill_from_task
            from aider.z.skills.grounding import build_grounding_pack

            rels = []
            abs_paths = []
            for path in edited:
                abs_paths.append(str(path))
                try:
                    rels.append(self.get_rel_fname(path))
                except Exception:
                    rels.append(str(path))

            diff = ""
            try:
                repo = getattr(self, "repo", None)
                if repo is not None:
                    if session_scoped_diff and hasattr(repo, "get_diffs_since"):
                        start = self._session_start_commit()
                        if start:
                            diff = repo.get_diffs_since(start, fnames=list(edited)) or ""
                        elif hasattr(repo, "get_diffs"):
                            diff = repo.get_diffs(fnames=list(edited)) or ""
                    elif hasattr(repo, "get_diffs"):
                        diff = repo.get_diffs(fnames=list(edited)) or ""
            except Exception:
                diff = ""

            root = None
            try:
                root = self.root if getattr(self, "root", None) else None
            except Exception:
                root = None

            pack = build_grounding_pack(
                user_request=user_message,
                files_changed=abs_paths or rels,
                root=root,
                diff=diff,
            )
            # Fallback text context if pack is empty (unreadable paths)
            context = (
                f"User request: {user_message}\n"
                f"Files changed: {', '.join(rels[:20])}\n"
            )
            topic = user_message.strip()
            if len(topic) > 200:
                topic = topic[:200] + "…"
            model_name = None
            if getattr(self, "main_model", None):
                model_name = getattr(self.main_model, "name", None)
            prefer_bug = bool(is_bugfix)
            skill, created = save_skill_from_task(
                self.io,
                topic,
                context=context,
                model_name=model_name,
                grounding_pack=pack if pack.files or pack.diff else None,
                uncertainty_engine=getattr(self, "uncertainty_engine", None),
                repo_root=root,
                prefer_bug_pattern=prefer_bug,
            )
            if created and skill:
                offer_view_new_skill(self.io, skill)
            # Refresh session index + Chroma
            try:
                from aider.z.skills.session import load_skills_for_session

                self.skill_index = load_skills_for_session(io=None)
            except Exception:
                pass
        except Exception as err:  # noqa: BLE001
            self._skill_capture_skip(f"exception: {err}")

    def _maybe_begin_uncertainty_task(self, user_message: str):
        engine = getattr(self, "uncertainty_engine", None)
        if not engine or not user_message or not isinstance(user_message, str):
            return
        # Avoid re-decomposing tiny follow-ups if a checklist is already active this session
        if engine.ctx.checklist and len(user_message) < 40:
            return
        try:
            from aider.z.uncertainty.checklist import (
                enrich_thin_checklist,
                format_checklist_for_user,
            )

            checklist = engine.begin_task(user_message)
            # New task → allow one drift confirm again
            self._drift_asked_this_task = False
            self._drift_reflection_log = []
            self._phase_spinner_update("Planning — enriching approach steps…")
            checklist, plan, was_thin = enrich_thin_checklist(checklist, user_message)
            engine.ctx.checklist = checklist
            if not checklist.items and not plan:
                return

            self._phase_spinner_stop()
            # Thin / greenfield: compact panel only (no checklist wall).
            # Verbose / Z_UX_FULL_PLAN_FIRST restores the scrollback dump.
            if was_thin and plan is not None:
                from aider.z.uncertainty.plan import (
                    format_plan_for_context,
                    format_plan_for_user,
                    format_thin_confirm,
                    interactive_plan_confirm,
                )
                from aider.z.uncertainty.schema import RequirementItem
                from aider.z.ux_preamble import ux_full_plan_first, ux_verbose

                if ux_verbose(coder=self) or ux_full_plan_first():
                    rendered = format_checklist_for_user(
                        checklist, plan=plan, thin=was_thin
                    )
                    self.io.tool_output("")
                    self.io.tool_output(rendered)
                    self.io.tool_output("")

                approved, plan = interactive_plan_confirm(
                    self.io,
                    plan,
                    question="Proceed with this approach?",
                    original_request=user_message,
                    checklist=checklist,
                    confirm_subject=format_thin_confirm(plan, checklist),
                )
                if not approved:
                    self.io.tool_warning(
                        "Approach rejected — reply in chat with corrections "
                        "(stack, Socket Mode vs webhook, commands, …)."
                    )
                    checklist.confirmed_by_user = False
                    return
                # Sync tracking checklist to the (possibly revised) plan
                if plan and plan.steps:
                    checklist.items = [
                        RequirementItem(text=s, kind="product")
                        for s in plan.steps[:7]
                        if s and not str(s).lower().startswith("do:")
                    ]
                checklist.confirmed_by_user = True
                try:
                    engine.ctx.plan = plan
                except Exception:
                    pass
                try:
                    block = format_plan_for_context(plan)
                    self.cur_messages += [
                        {"role": "user", "content": block},
                        {
                            "role": "assistant",
                            "content": (
                                "I'll follow that approach and the tracking "
                                "checklist. Tell me if you want further changes "
                                "before I edit."
                            ),
                        },
                    ]
                except Exception:
                    pass
                self.io.tool_output(
                    "Approach noted — proceeding. Say what to change anytime "
                    "before edits if the specs are still wrong."
                )
                try:
                    pre = getattr(self, "_turn_preamble", None)
                    if pre is not None:
                        pre.note_plan(gated=True, approved=True)
                except Exception:
                    pass
            elif checklist.items:
                # Structured checklist already meaningful — mark seen
                # Quiet mode: skip dumping the checklist wall into scrollback.
                from aider.z.ux_preamble import ux_verbose

                if ux_verbose(coder=self):
                    rendered = format_checklist_for_user(
                        checklist, plan=plan, thin=was_thin
                    )
                    self.io.tool_output("")
                    self.io.tool_output(rendered)
                    self.io.tool_output("")
                checklist.confirmed_by_user = True
        except Exception:
            pass

    def _reflection_edited_rels(self) -> list[str]:
        """Repo-relative paths touched this send (fallback: session edits)."""
        last_send = getattr(self, "_last_send_edited_files", None)
        used_fallback = not bool(last_send)
        edited = set(last_send or ())
        if not edited:
            edited = set(self.aider_edited_files or ())
        if not edited:
            self._detector_debug(
                f"_last_send_edited_files={sorted(last_send or []) or '[]'} "
                f"aider_edited_files={sorted(self.aider_edited_files or []) or '[]'} "
                f"rels=[] (empty)"
            )
            return []
        rels = []
        for path in edited:
            try:
                rels.append(self.get_rel_fname(path))
            except Exception:
                rels.append(str(path))
        self._detector_debug(
            f"_last_send_edited_files={sorted(last_send or []) or '[]'} "
            f"used_session_fallback={used_fallback} "
            f"aider_edited_files={sorted(self.aider_edited_files or []) or '[]'} "
            f"rels={sorted(rels) or '[]'}"
        )
        return rels

    def _rescore_checklist_for_drift(self) -> None:
        """Bind evidence + rescore checklist during reflections (drift only).

        Does not run edge-case detectors, create nodes, or print summaries —
        that stays behind the clean-exit ``_run_uncertainty_analysis`` gate.
        """
        eng = getattr(self, "uncertainty_engine", None)
        checklist = getattr(getattr(eng, "ctx", None), "checklist", None)
        if not eng or not checklist:
            return
        rels = self._reflection_edited_rels()
        if not rels:
            return
        edited_abs = getattr(self, "_last_send_edited_files", None) or set(
            self.aider_edited_files or ()
        )
        try:
            repo = getattr(self, "repo", None)
            if repo is not None and hasattr(repo, "get_diffs") and edited_abs:
                eng.record_diff(repo.get_diffs(fnames=list(edited_abs)) or "")
        except Exception:
            pass
        eng.rescore_checklist_light(
            rels,
            tests_passed=getattr(self, "test_outcome", None),
        )
        self._drift_debug(
            "rescored checklist for drift "
            f"(files={len(rels)} n_reflections={getattr(self, 'num_reflections', 0)})"
        )

    def _run_cheap_detectors_for_reflection(self) -> None:
        """Run deterministic detectors while reflections are still pending.

        Closes the gap where exhaustion / never-clean-exit tasks skipped
        ``analyze_edits`` entirely — so absorption / sibling / established-
        solution checks never ran. Does not invoke model-backed edge cases.
        """
        eng = getattr(self, "uncertainty_engine", None)
        if not eng:
            self._detector_debug(
                f"no uncertainty_engine, skipping cheap detector pass "
                f"(n_reflections={getattr(self, 'num_reflections', 0)})"
            )
            return
        rels = self._reflection_edited_rels()
        n_ref = getattr(self, "num_reflections", 0)
        self._detector_debug(
            f"rels={sorted(rels) or '[]'} len={len(rels)} n_reflections={n_ref}"
        )
        if not rels:
            self._detector_debug(
                f"rels empty, skipping cheap detector pass "
                f"(n_reflections={n_ref})"
            )
            return
        edited_abs = getattr(self, "_last_send_edited_files", None) or set(
            self.aider_edited_files or ()
        )
        try:
            repo = getattr(self, "repo", None)
            if repo is not None and hasattr(repo, "get_diffs") and edited_abs:
                eng.record_diff(repo.get_diffs(fnames=list(edited_abs)) or "")
        except Exception:
            pass
        self._detector_debug(
            f"calling analyze_edits(cheap_only=True) "
            f"rels={sorted(rels) or '[]'} n_reflections={n_ref}"
        )
        new_nodes = eng.analyze_edits(
            rels,
            tests_passed=getattr(self, "test_outcome", None),
            run_gap_analysis=False,
            cheap_only=True,
        )
        try:
            n_nodes = len(new_nodes or [])
        except TypeError:
            n_nodes = 0
        self._detector_debug(
            f"analyze_edits returned n_nodes={n_nodes} "
            f"n_reflections={n_ref}"
        )

    def _record_drift_reflection_turn(
        self,
        *,
        was_reflection: bool,
        files_before: set,
        status_before: dict,
        evidence_before: dict | None = None,
    ) -> None:
        """Append one reflection-turn sample after send_message returns."""
        if not was_reflection:
            return
        from aider.z.uncertainty.drift import (
            ReflectionTurn,
            checklist_progressed,
            evidence_stagnant,
            multi_turn_stagnant,
            off_scope_edits,
            status_snapshot,
        )

        eng = getattr(self, "uncertainty_engine", None)
        checklist = getattr(getattr(eng, "ctx", None), "checklist", None)
        if not checklist:
            return
        # Prefer this send's apply_updates() set so re-editing an already-touched
        # file (post-fix creep in the same path) still counts as a turn edit.
        last_send = getattr(self, "_last_send_edited_files", None)
        if last_send is not None:
            files_delta = set(last_send)
        else:
            files_delta = set(self.aider_edited_files or ()) - set(files_before or ())
        # Prefer relative paths for scope matching
        rels = set()
        for f in files_delta:
            try:
                rels.add(self.get_rel_fname(f))
            except Exception:
                rels.add(str(f))
        progressed = checklist_progressed(status_before, checklist)
        # If prepare_commit didn't rescore, still compare current snapshot
        if not progressed and status_before:
            after = status_snapshot(checklist)
            if after != status_before:
                progressed = checklist_progressed(status_before, checklist)
        turn_stagnant = evidence_stagnant(evidence_before or {}, checklist)
        self._drift_debug(
            f"turn_stagnant={sorted(turn_stagnant) or '[]'} "
            f"n_reflections={getattr(self, 'num_reflections', 0)}"
        )
        log = getattr(self, "_drift_reflection_log", None)
        if log is None:
            self._drift_reflection_log = []
            log = self._drift_reflection_log
        # Practically resolved for scope: stagnant across the last 2 reflections
        exclude_ids = multi_turn_stagnant(log, turn_stagnant, window=2)
        if self._drift_debug_enabled():
            recent = []
            for turn in list(log)[-2:]:
                recent.append(
                    "{"
                    f"files={sorted(turn.files) or []}, "
                    f"progressed={turn.progressed}, "
                    f"stagnant_ids={sorted(turn.stagnant_ids) or []}"
                    "}"
                )
            self._drift_debug(
                f"exclude_ids={sorted(exclude_ids) or '[]'} "
                f"log_len={len(log)} recent=[{'; '.join(recent) or 'none'}]"
            )
        # Per-file +/- changed lines so off_scope_edits can reject
        # right-file / wrong-reason edits (symbol/hunk match).
        diff_by_file = None
        try:
            repo = getattr(self, "repo", None)
            if repo is not None and hasattr(repo, "get_diffs") and files_delta:
                from aider.z.uncertainty.checklist import _diff_changed_by_file

                combined = repo.get_diffs(fnames=list(files_delta)) or ""
                raw = _diff_changed_by_file(combined)
                if raw:
                    diff_by_file = {}
                    for path, blob in raw.items():
                        try:
                            rel = self.get_rel_fname(path)
                        except Exception:
                            rel = str(path)
                        diff_by_file[str(path)] = blob
                        diff_by_file[str(rel)] = blob
        except Exception:
            diff_by_file = None
        off = off_scope_edits(
            rels,
            checklist,
            stagnant_ids=exclude_ids,
            diff_by_file=diff_by_file,
        )
        self._drift_debug(
            f"rels={sorted(rels) or '[]'} off_scope={sorted(off) or '[]'} "
            f"progressed={progressed}"
        )
        log.append(
            ReflectionTurn(
                files=rels,
                progressed=progressed,
                off_scope=list(off),
                stagnant_ids=set(turn_stagnant),
            )
        )

    def _maybe_detect_drift(self):
        """Confirm-gated drift response when reflections leave the checklist.

        Returns ``DriftConfirmResult`` when the user accepts (refocus message
        and/or ``stop=True`` for post-completion creep), else ``None`` (and may
        record a Medium uncertainty node on decline).
        """
        # Need at least one completed reflection turn in the log (was_reflection)
        if int(getattr(self, "num_reflections", 0) or 0) < 1:
            return None
        if getattr(self, "_drift_asked_this_task", False):
            return None
        eng = getattr(self, "uncertainty_engine", None)
        checklist = getattr(getattr(eng, "ctx", None), "checklist", None)
        if not checklist or not checklist.items:
            return None

        from aider.z.uncertainty.drift import (
            DriftConfirmResult,
            confirm_prompt,
            detect_drift,
            format_refocus_message,
            is_complete_task_creep,
            make_drift_observed_node,
        )

        history = list(getattr(self, "_drift_reflection_log", None) or [])
        if not history:
            return None
        signal = detect_drift(history, checklist)
        if self._drift_debug_enabled():
            if signal is None:
                recent = []
                for turn in history[-2:]:
                    recent.append(
                        "{"
                        f"files={sorted(turn.files) or []}, "
                        f"progressed={turn.progressed}, "
                        f"off_scope={sorted(turn.off_scope) or []}, "
                        f"stagnant_ids={sorted(turn.stagnant_ids) or []}"
                        "}"
                    )
                self._drift_debug(
                    f"detect_drift=None history_len={len(history)} "
                    f"window=[{'; '.join(recent) or 'none'}]"
                )
            else:
                self._drift_debug(
                    f"detect_drift=SIGNAL off_scope={signal.off_scope_files!r} "
                    f"unresolved={len(signal.unresolved)} "
                    f"summary={signal.summary[:120]!r}"
                )
        if not signal:
            return None

        self._drift_asked_this_task = True
        prompt = confirm_prompt(signal)
        try:
            # Long drift text goes in the escalation panel (subject); keep the
            # prompt_toolkit line short so terminal resize does not garble it.
            accepted = self.io.confirm_ask(
                "Refocus on the original task?",
                subject=prompt,
                default="n",
                explicit_yes_required=True,
            )
        except Exception:
            accepted = False

        if accepted:
            if is_complete_task_creep(signal):
                try:
                    self.io.tool_output(
                        "Task looks complete — stopping further unrequested edits."
                    )
                except Exception:
                    pass
                return DriftConfirmResult(stop=True)
            refocus = format_refocus_message(signal)
            try:
                self.io.tool_output("Refocusing on still-open checklist items…")
            except Exception:
                pass
            return DriftConfirmResult(refocus_message=refocus)

        # Declined / --yes-always default-n: continue, but leave a Medium breadcrumb
        try:
            node = make_drift_observed_node(
                signal,
                task_id=getattr(getattr(eng, "ctx", None), "current_task_id", None),
                task_title=getattr(
                    getattr(eng, "ctx", None), "current_task_title", None
                ),
                session_id=getattr(getattr(eng, "ctx", None), "session_id", None),
            )
            store = getattr(self, "uncertainty_store", None) or getattr(
                eng, "store", None
            )
            if store is not None:
                store.add(node)
            self.io.tool_warning(
                "Drift observed — continuing without refocus "
                "(see /uncertainties)."
            )
        except Exception:
            pass
        return None

    def _maybe_require_implementation_plan(self, user_message: str) -> bool:
        """
        Priority 3 gated planning: for high-stakes / high-blast-radius tasks,
        force a reviewable plan before any diff. Returns False if the user
        rejects the plan (caller should abort the turn).
        """
        engine = getattr(self, "uncertainty_engine", None)
        if not engine or not user_message or not isinstance(user_message, str):
            return True
        planning_required = True  # we only enter here when mode.allows_planning
        try:
            from aider.z.uncertainty.detectors import count_symbol_references
            from aider.z.uncertainty.plan import (
                format_plan_for_context,
                format_plan_for_user,
            )

            files = []
            try:
                files = list(self.get_inchat_relative_files() or [])
            except Exception:
                files = []

            self._phase_spinner_update("Planning — scoring blast radius…")

            # Light pre-edit blast-radius: reference count for symbols in chat files
            symbols = []
            reference_count = 0
            try:
                contents = {}
                root = Path(getattr(self, "root", None) or ".")
                for rel in files[:12]:
                    path = root / rel
                    if path.is_file() and str(rel).endswith((".py", ".ts", ".js", ".tsx", ".jsx")):
                        try:
                            contents[rel] = path.read_text(encoding="utf-8", errors="ignore")[
                                :20000
                            ]
                        except OSError:
                            pass
                symbols = engine._extract_symbols(contents) if contents else []
                for sym in symbols[:3]:
                    reference_count = max(
                        reference_count,
                        count_symbol_references(root, sym, exclude_files=files),
                    )
            except Exception:
                pass

            self._phase_spinner_update("Planning — drafting implementation plan…")
            plan = engine.maybe_require_plan(
                user_message,
                files=files,
                symbols=symbols,
                reference_count=reference_count,
            )
            if plan is None:
                return True

            # Compact-first confirm (full plan via View / verbose / escape hatch)
            self._phase_spinner_stop()
            from aider.z.ux_preamble import ux_full_plan_first, ux_verbose

            if ux_verbose(coder=self) or ux_full_plan_first():
                rendered = format_plan_for_user(plan)
                self.io.tool_output("")
                self.io.tool_output(rendered)
                self.io.tool_output("")

            # Interactive: Yes / No / Change / View.
            approved = True
            if getattr(self.io, "yes", None) is not True:
                from aider.z.uncertainty.plan import interactive_plan_confirm

                try:
                    pre = getattr(self, "_turn_preamble", None)
                    if pre is not None:
                        pre.note_plan(gated=True)
                except Exception:
                    pass

                approved, plan = interactive_plan_confirm(
                    self.io,
                    plan,
                    question="Proceed with this implementation plan?",
                    original_request=user_message,
                )
            if not approved:
                self.io.tool_warning(
                    "Plan rejected — no edits will be written for this request. "
                    "Reply with the specs you want changed, then ask again."
                )
                engine.record_user_decision(f"rejected plan: {plan.title}")
                engine.ctx.plan_approved = False
                engine.ctx.plan_required = True
                engine.ctx.plan = plan
                return False

            engine.approve_plan(plan)
            block = format_plan_for_context(plan)
            revisions = [
                a.resolution
                for a in (plan.ambiguities or [])
                if (a.ambiguity or "").lower().startswith("user revised")
            ]
            revision_note = ""
            if revisions:
                revision_note = (
                    " Incorporating your plan revisions: "
                    + "; ".join(revisions[-3:])
                )
            self.cur_messages += [
                {"role": "user", "content": block},
                {
                    "role": "assistant",
                    "content": (
                        "I'll treat that plan as binding: validation contracts, "
                        "input domains, invariants, and ambiguity resolutions "
                        "before writing any diff."
                        + revision_note
                    ),
                },
            ]
            self.io.tool_output("Plan approved — proceeding with implementation.")
            try:
                pre = getattr(self, "_turn_preamble", None)
                if pre is not None:
                    pre.note_plan(gated=True, approved=True)
            except Exception:
                pass
            return True
        except Exception as err:
            # P1.3 — planning failures are not silently "success"
            try:
                from aider.z.errors import (
                    IntegrityGateError,
                    OptionalSubsystemError,
                    RecoverableAgentError,
                    handle_classified,
                )

                if isinstance(err, OptionalSubsystemError):
                    policy = handle_classified(err, context="planning", io=self.io)
                    return True
                wrapped = RecoverableAgentError(str(err))
                policy = handle_classified(
                    wrapped,
                    context="planning",
                    io=self.io,
                    planning_required=planning_required,
                )
                if policy == "fail_closed":
                    return False
                return True
            except Exception:
                self.io.tool_warning(f"Planning gate error: {err}")
                return False

    def check_and_open_urls(self, exc, friendly_msg=None):
        """Check exception for URLs, offer to open in a browser, with user-friendly error msgs."""
        text = str(exc)

        if friendly_msg:
            self.io.tool_warning(text)
            self.io.tool_error(f"{friendly_msg}")
        else:
            self.io.tool_error(text)

        # Exclude double quotes from the matched URL characters
        url_pattern = re.compile(r'(https?://[^\s/$.?#].[^\s"]*)')
        urls = list(set(url_pattern.findall(text)))  # Use set to remove duplicates
        for url in urls:
            url = url.rstrip(".',\"}")  # Added } to the characters to strip
            self.io.offer_url(url)
        return urls

    def check_for_urls(self, inp: str) -> List[str]:
        """Check input for URLs and offer to add them to the chat."""
        if not self.detect_urls:
            return inp

        # Exclude double quotes from the matched URL characters
        url_pattern = re.compile(r'(https?://[^\s/$.?#].[^\s"]*[^\s,.])')
        urls = list(set(url_pattern.findall(inp)))  # Use set to remove duplicates
        group = ConfirmGroup(urls)
        for url in urls:
            if url not in self.rejected_urls:
                url = url.rstrip(".',\"")
                if self.io.confirm_ask(
                    "Add URL to the chat?", subject=url, group=group, allow_never=True
                ):
                    inp += "\n\n"
                    inp += self.commands.cmd_web(url, return_content=True)
                else:
                    self.rejected_urls.add(url)

        return inp

    def keyboard_interrupt(self):
        # Ensure cursor is visible on exit; stop any busy spinner immediately.
        self._phase_spinner_stop()
        Console().show_cursor(True)

        now = time.time()

        thresh = 2  # seconds
        if self.last_keyboard_interrupt and now - self.last_keyboard_interrupt < thresh:
            self.io.tool_warning("\n\n^C KeyboardInterrupt")
            self.event("exit", reason="Control-C")
            sys.exit()

        self.io.tool_warning("\n\n^C again to exit  (or wait — agent work was interrupted)")

        self.last_keyboard_interrupt = now

    def summarize_start(self):
        if not self.summarizer.too_big(self.done_messages):
            return

        self.summarize_end()

        if self.verbose:
            self.io.tool_output("Starting to summarize chat history.")

        self.summarizer_thread = threading.Thread(target=self.summarize_worker)
        self.summarizer_thread.start()

    def summarize_worker(self):
        self.summarizing_messages = list(self.done_messages)
        try:
            self.summarized_done_messages = self.summarizer.summarize(self.summarizing_messages)
        except ValueError as err:
            self.io.tool_warning(err.args[0])

        if self.verbose:
            self.io.tool_output("Finished summarizing chat history.")

    def summarize_end(self):
        if self.summarizer_thread is None:
            return

        self.summarizer_thread.join()
        self.summarizer_thread = None

        if self.summarizing_messages == self.done_messages:
            self.done_messages = self.summarized_done_messages
        self.summarizing_messages = None
        self.summarized_done_messages = []

    def move_back_cur_messages(self, message):
        self.done_messages += self.cur_messages
        self.summarize_start()

        # TODO check for impact on image messages
        if message:
            self.done_messages += [
                dict(role="user", content=message),
                dict(role="assistant", content="Ok."),
            ]
        self.cur_messages = []

    def normalize_language(self, lang_code):
        """
        Convert a locale code such as ``en_US`` or ``fr`` into a readable
        language name (e.g. ``English`` or ``French``).  If Babel is
        available it is used for reliable conversion; otherwise a small
        built-in fallback map handles common languages.
        """
        if not lang_code:
            return None

        if lang_code.upper() in ("C", "POSIX"):
            return None

        # Probably already a language name
        if (
            len(lang_code) > 3
            and "_" not in lang_code
            and "-" not in lang_code
            and lang_code[0].isupper()
        ):
            return lang_code

        # Preferred: Babel
        if Locale is not None:
            try:
                loc = Locale.parse(lang_code.replace("-", "_"))
                return loc.get_display_name("en").capitalize()
            except Exception:
                pass  # Fall back to manual mapping

        # Simple fallback for common languages
        fallback = {
            "en": "English",
            "fr": "French",
            "es": "Spanish",
            "de": "German",
            "it": "Italian",
            "pt": "Portuguese",
            "zh": "Chinese",
            "ja": "Japanese",
            "ko": "Korean",
            "ru": "Russian",
        }
        primary_lang_code = lang_code.replace("-", "_").split("_")[0].lower()
        return fallback.get(primary_lang_code, lang_code)

    def get_user_language(self):
        """
        Detect the user's language preference and return a human-readable
        language name such as ``English``. Detection order:

        1. ``self.chat_language`` if explicitly set
        2. ``locale.getlocale()``
        3. ``LANG`` / ``LANGUAGE`` / ``LC_ALL`` / ``LC_MESSAGES`` environment variables
        """

        # Explicit override
        if self.chat_language:
            return self.normalize_language(self.chat_language)

        # System locale
        try:
            lang = locale.getlocale()[0]
            if lang:
                lang = self.normalize_language(lang)
            if lang:
                return lang
        except Exception:
            pass

        # Environment variables
        for env_var in ("LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES"):
            lang = os.environ.get(env_var)
            if lang:
                lang = lang.split(".")[0]  # Strip encoding if present
                return self.normalize_language(lang)

        return None

    def get_platform_info(self):
        platform_text = ""
        try:
            platform_text = f"- Platform: {platform.platform()}\n"
        except KeyError:
            # Skip platform info if it can't be retrieved
            platform_text = "- Platform information unavailable\n"

        shell_var = "COMSPEC" if os.name == "nt" else "SHELL"
        shell_val = os.getenv(shell_var)
        platform_text += f"- Shell: {shell_var}={shell_val}\n"

        user_lang = self.get_user_language()
        if user_lang:
            platform_text += f"- Language: {user_lang}\n"

        dt = datetime.now().astimezone().strftime("%Y-%m-%d")
        platform_text += f"- Current date: {dt}\n"

        if self.repo:
            platform_text += "- The user is operating inside a git repository\n"

        if self.lint_cmds:
            if self.auto_lint:
                platform_text += (
                    "- The user's pre-commit runs these lint commands, don't suggest running"
                    " them:\n"
                )
            else:
                platform_text += "- The user prefers these lint commands:\n"
            for lang, cmd in self.lint_cmds.items():
                if lang is None:
                    platform_text += f"  - {cmd}\n"
                else:
                    platform_text += f"  - {lang}: {cmd}\n"

        if self.test_cmd:
            if self.auto_test:
                platform_text += (
                    "- The user's pre-commit runs this test command, don't suggest running them: "
                )
            else:
                platform_text += "- The user prefers this test command: "
            platform_text += self.test_cmd + "\n"

        return platform_text

    def fmt_system_prompt(self, prompt):
        final_reminders = []
        # Always-on: never fabricate local stand-ins for real third-party packages
        dep_rule = getattr(self.gpt_prompts, "dependency_fabrication_prompt", None)
        if dep_rule:
            final_reminders.append(dep_rule)
        # Soft guidance: keep sys.exit / process kill out of reusable core
        core_rule = getattr(self.gpt_prompts, "core_adapter_prompt", None)
        if core_rule:
            final_reminders.append(core_rule)
        # OpenCode-inspired coding discipline — implement modes only (keep ask thin)
        try:
            from aider.z.coding_context import coding_quality_reminder
            from aider.z.task_mode import TaskMode

            mode = getattr(self, "task_mode", None)
            allows = True
            if mode is not None:
                allows = bool(getattr(mode, "allows_edits", True))
            # Also skip for explicit ask/context edit formats
            fmt = getattr(self, "edit_format", None)
            if fmt in ("ask", "context", "help", "plan"):
                allows = False
            if allows and mode not in (TaskMode.ASK, TaskMode.PLAN):
                final_reminders.append(coding_quality_reminder())
            if mode is TaskMode.PLAN:
                from aider.z.plan_mode import format_plan_mode_reminder

                final_reminders.append(format_plan_mode_reminder())
        except Exception:
            pass
        if self.main_model.lazy:
            final_reminders.append(self.gpt_prompts.lazy_prompt)
        if self.main_model.overeager:
            final_reminders.append(self.gpt_prompts.overeager_prompt)

        user_lang = self.get_user_language()
        if user_lang:
            final_reminders.append(f"Reply in {user_lang}.\n")

        platform_text = self.get_platform_info()

        if self.suggest_shell_commands:
            shell_cmd_prompt = self.gpt_prompts.shell_cmd_prompt.format(platform=platform_text)
            shell_cmd_reminder = self.gpt_prompts.shell_cmd_reminder.format(platform=platform_text)
            rename_with_shell = self.gpt_prompts.rename_with_shell
        else:
            shell_cmd_prompt = self.gpt_prompts.no_shell_cmd_prompt.format(platform=platform_text)
            shell_cmd_reminder = self.gpt_prompts.no_shell_cmd_reminder.format(
                platform=platform_text
            )
            rename_with_shell = ""

        if user_lang:  # user_lang is the result of self.get_user_language()
            language = user_lang
        else:
            language = "the same language they are using"  # Default if no specific lang detected

        if self.fence[0] == "`" * 4:
            quad_backtick_reminder = (
                "\nIMPORTANT: Use *quadruple* backticks ```` as fences, not triple backticks!\n"
            )
        else:
            quad_backtick_reminder = ""

        final_reminders = "\n\n".join(final_reminders)

        prompt = prompt.format(
            fence=self.fence,
            quad_backtick_reminder=quad_backtick_reminder,
            final_reminders=final_reminders,
            platform=platform_text,
            shell_cmd_prompt=shell_cmd_prompt,
            rename_with_shell=rename_with_shell,
            shell_cmd_reminder=shell_cmd_reminder,
            go_ahead_tip=self.gpt_prompts.go_ahead_tip,
            language=language,
        )

        return prompt

    def format_chat_chunks(self):
        self.choose_fence()
        main_sys = self.fmt_system_prompt(self.gpt_prompts.main_system)
        if self.main_model.system_prompt_prefix:
            main_sys = self.main_model.system_prompt_prefix + "\n" + main_sys

        example_messages = []
        if self.main_model.examples_as_sys_msg:
            if self.gpt_prompts.example_messages:
                main_sys += "\n# Example conversations:\n\n"
            for msg in self.gpt_prompts.example_messages:
                role = msg["role"]
                content = self.fmt_system_prompt(msg["content"])
                main_sys += f"## {role.upper()}: {content}\n\n"
            main_sys = main_sys.strip()
        else:
            for msg in self.gpt_prompts.example_messages:
                example_messages.append(
                    dict(
                        role=msg["role"],
                        content=self.fmt_system_prompt(msg["content"]),
                    )
                )
            if self.gpt_prompts.example_messages:
                example_messages += [
                    dict(
                        role="user",
                        content=(
                            "I switched to a new code base. Please don't consider the above files"
                            " or try to edit them any longer."
                        ),
                    ),
                    dict(role="assistant", content="Ok."),
                ]

        if self.gpt_prompts.system_reminder:
            main_sys += "\n" + self.fmt_system_prompt(self.gpt_prompts.system_reminder)

        chunks = ChatChunks()

        if self.main_model.use_system_prompt:
            chunks.system = [
                dict(role="system", content=main_sys),
            ]
        else:
            chunks.system = [
                dict(role="user", content=main_sys),
                dict(role="assistant", content="Ok."),
            ]

        chunks.examples = example_messages

        self.summarize_end()
        chunks.done = self.done_messages

        chunks.repo = self.get_repo_messages()
        chunks.readonly_files = self.get_readonly_files_messages()
        chunks.chat_files = self.get_chat_files_messages()

        if self.gpt_prompts.system_reminder:
            reminder_message = [
                dict(
                    role="system", content=self.fmt_system_prompt(self.gpt_prompts.system_reminder)
                ),
            ]
        else:
            reminder_message = []

        chunks.cur = list(self.cur_messages)
        chunks.reminder = []

        # TODO review impact of token count on image messages
        messages_tokens = self.main_model.token_count(chunks.all_messages())
        reminder_tokens = self.main_model.token_count(reminder_message)
        cur_tokens = self.main_model.token_count(chunks.cur)

        if None not in (messages_tokens, reminder_tokens, cur_tokens):
            total_tokens = messages_tokens + reminder_tokens + cur_tokens
        else:
            # add the reminder anyway
            total_tokens = 0

        if chunks.cur:
            final = chunks.cur[-1]
        else:
            final = None

        max_input_tokens = self.main_model.info.get("max_input_tokens") or 0
        # Add the reminder prompt if we still have room to include it.
        if (
            not max_input_tokens
            or total_tokens < max_input_tokens
            and self.gpt_prompts.system_reminder
        ):
            if self.main_model.reminder == "sys":
                chunks.reminder = reminder_message
            elif self.main_model.reminder == "user" and final and final["role"] == "user":
                # stuff it into the user message
                new_content = (
                    final["content"]
                    + "\n\n"
                    + self.fmt_system_prompt(self.gpt_prompts.system_reminder)
                )
                chunks.cur[-1] = dict(role=final["role"], content=new_content)

        return chunks

    def format_messages(self):
        chunks = self.format_chat_chunks()
        if self.add_cache_headers:
            chunks.add_cache_control_headers()

        return chunks

    def warm_cache(self, chunks):
        if not self.add_cache_headers:
            return
        if not self.num_cache_warming_pings:
            return
        if not self.ok_to_warm_cache:
            return

        delay = 5 * 60 - 5
        delay = float(os.environ.get("AIDER_CACHE_KEEPALIVE_DELAY", delay))
        self.next_cache_warm = time.time() + delay
        self.warming_pings_left = self.num_cache_warming_pings
        self.cache_warming_chunks = chunks

        if self.cache_warming_thread:
            return

        def warm_cache_worker():
            while self.ok_to_warm_cache:
                time.sleep(1)
                if self.warming_pings_left <= 0:
                    continue
                now = time.time()
                if now < self.next_cache_warm:
                    continue

                self.warming_pings_left -= 1
                self.next_cache_warm = time.time() + delay

                kwargs = dict(self.main_model.extra_params) or dict()
                kwargs["max_tokens"] = 1

                try:
                    completion = litellm.completion(
                        model=self.main_model.name,
                        messages=self.cache_warming_chunks.cacheable_messages(),
                        stream=False,
                        **kwargs,
                    )
                except Exception as err:
                    self.io.tool_warning(f"Cache warming error: {str(err)}")
                    continue

                cache_hit_tokens = getattr(
                    completion.usage, "prompt_cache_hit_tokens", 0
                ) or getattr(completion.usage, "cache_read_input_tokens", 0)

                if self.verbose:
                    self.io.tool_output(f"Warmed {format_tokens(cache_hit_tokens)} cached tokens.")

        self.cache_warming_thread = threading.Timer(0, warm_cache_worker)
        self.cache_warming_thread.daemon = True
        self.cache_warming_thread.start()

        return chunks

    def check_tokens(self, messages):
        """Check if the messages will fit within the model's token limits."""
        input_tokens = self.main_model.token_count(messages)
        max_input_tokens = self.main_model.info.get("max_input_tokens") or 0

        if max_input_tokens and input_tokens >= max_input_tokens:
            self.io.tool_error(
                f"Your estimated chat context of {input_tokens:,} tokens exceeds the"
                f" {max_input_tokens:,} token limit for {self.main_model.name}!"
            )
            self.io.tool_output("To reduce the chat context:")
            self.io.tool_output("- Use /drop to remove unneeded files from the chat")
            self.io.tool_output("- Use /clear to clear the chat history")
            self.io.tool_output("- Break your code into smaller files")
            self.io.tool_output(
                "It's probably safe to try and send the request, most providers won't charge if"
                " the context limit is exceeded."
            )

            if not self.io.confirm_ask("Try to proceed anyway?"):
                return False
        return True

    def send_message(self, inp):
        self.event("message_send_starting")
        self._last_send_edited_files = set()

        # Notify IO that LLM processing is starting
        self.io.llm_started()

        self.cur_messages += [
            dict(role="user", content=inp),
        ]

        chunks = self.format_messages()
        messages = chunks.all_messages()
        if not self.check_tokens(messages):
            return
        self.warm_cache(chunks)

        if self.verbose:
            utils.show_messages(messages, functions=self.functions)

        self.multi_response_content = ""
        if self.show_pretty():
            spinner_text = "Waiting for " + self.main_model.name
            if getattr(self.io, "z_theme", True):
                spinner_text = f"{spinner_text}  · Ctrl+C to interrupt"
                self.waiting_spinner = waiting_display(spinner_text)
            else:
                self.waiting_spinner = WaitingSpinner(spinner_text)
            self.waiting_spinner.start()
            try:
                self.io.agent_busy = True
                self.io._stop_agent_busy = self._stop_waiting_spinner
            except Exception:
                pass
            if self.stream:
                self.mdstream = self.io.get_assistant_mdstream()
            else:
                self.mdstream = None
        else:
            self.mdstream = None

        retry_delay = 0.125

        litellm_ex = LiteLLMExceptions()

        self.usage_report = None
        exhausted = False
        interrupted = False
        try:
            while True:
                try:
                    yield from self.send(messages, functions=self.functions)
                    break
                except litellm_ex.exceptions_tuple() as err:
                    ex_info = litellm_ex.get_ex_info(err)

                    if ex_info.name == "ContextWindowExceededError":
                        exhausted = True
                        break

                    should_retry = ex_info.retry
                    if should_retry:
                        retry_delay *= 2
                        if retry_delay > RETRY_TIMEOUT:
                            should_retry = False

                    if not should_retry:
                        self.mdstream = None
                        self.check_and_open_urls(err, ex_info.description)
                        break

                    err_msg = str(err)
                    if ex_info.description:
                        self.io.tool_warning(err_msg)
                        self.io.tool_error(ex_info.description)
                    else:
                        self.io.tool_error(err_msg)

                    self.io.tool_output(f"Retrying in {retry_delay:.1f} seconds...")
                    time.sleep(retry_delay)
                    continue
                except KeyboardInterrupt:
                    interrupted = True
                    break
                except FinishReasonLength:
                    # We hit the output limit!
                    if not self.main_model.info.get("supports_assistant_prefill"):
                        exhausted = True
                        break

                    self.multi_response_content = self.get_multi_response_content_in_progress()

                    if messages[-1]["role"] == "assistant":
                        messages[-1]["content"] = self.multi_response_content
                    else:
                        messages.append(
                            dict(role="assistant", content=self.multi_response_content, prefix=True)
                        )
                except Exception as err:
                    self.mdstream = None
                    lines = traceback.format_exception(type(err), err, err.__traceback__)
                    self.io.tool_warning("".join(lines))
                    self.io.tool_error(str(err))
                    self.event("message_send_exception", exception=str(err))
                    return
        finally:
            if self.mdstream:
                self.live_incremental_response(True)
                self.mdstream = None

            # Ensure any waiting spinner is stopped
            self._stop_waiting_spinner()

            self.partial_response_content = self.get_multi_response_content_in_progress(True)
            self.remove_reasoning_content()
            self.multi_response_content = ""

        ###
        # print()
        # print("=" * 20)
        # dump(self.partial_response_content)

        self.io.tool_output()

        self.show_usage_report()

        self.add_assistant_reply_to_cur_messages()

        if exhausted:
            if self.cur_messages and self.cur_messages[-1]["role"] == "user":
                self.cur_messages += [
                    dict(
                        role="assistant",
                        content="FinishReasonLength exception: you sent too many tokens",
                    ),
                ]

            self.show_exhausted_error()
            self.num_exhausted_context_windows += 1
            return

        if self.partial_response_function_call:
            args = self.parse_partial_args()
            if args:
                content = args.get("explanation") or ""
            else:
                content = ""
        elif self.partial_response_content:
            content = self.partial_response_content
        else:
            content = ""

        if not interrupted:
            # Claude eval Finding 1: "Plan approved" then "please add these
            # files…" must not stall — auto-add existing paths and reflect
            # into implementation (interactive and --yes-always).
            try:
                from aider.z.ni_contract import (
                    detect_add_files_miss,
                    maybe_auto_seed_reflect,
                )

                if detect_add_files_miss(content) and maybe_auto_seed_reflect(
                    self,
                    user_message=getattr(self, "_z_ni_user_message", None) or "",
                    assistant_text=content,
                ):
                    return
            except Exception:
                pass

            add_rel_files_message = self.check_for_file_mentions(content)
            if add_rel_files_message:
                if self.reflected_message:
                    self.reflected_message += "\n\n" + add_rel_files_message
                else:
                    self.reflected_message = add_rel_files_message
                return

            try:
                if self.reply_completed():
                    return
            except KeyboardInterrupt:
                interrupted = True

            # Thin read-only tool-loop before applying SEARCH/REPLACE
            if not interrupted and self._maybe_run_tool_loop(content):
                return

        if interrupted:
            if self.cur_messages and self.cur_messages[-1]["role"] == "user":
                self.cur_messages[-1]["content"] += "\n^C KeyboardInterrupt"
            else:
                self.cur_messages += [dict(role="user", content="^C KeyboardInterrupt")]
            self.cur_messages += [
                dict(role="assistant", content="I see that you interrupted my previous reply.")
            ]
            return

        edited = self.apply_updates()
        # Per-send edit set for drift detection (same file re-touched still counts)
        self._last_send_edited_files = set(edited or ())

        if edited:
            self.aider_edited_files.update(edited)

        if self.reflected_message:
            return

        # Fallback auto-seed (path mentions / NI) when no edits landed
        if not edited:
            try:
                from aider.z.ni_contract import maybe_auto_seed_reflect

                if maybe_auto_seed_reflect(
                    self,
                    user_message=getattr(self, "_z_ni_user_message", None) or "",
                    assistant_text=content,
                ):
                    return
            except Exception:
                pass

        # Reflection turns that apply no new patches must still gate/commit
        # earlier session edits (fmtlog4: lint-fix reflection replied with
        # prose → empty apply_updates → bool(edited) skipped prepare_commit
        # and left the real fix uncommitted with no message).
        from aider.z.uncertainty.gate import resolve_commit_edit_set

        commit_edited = resolve_commit_edit_set(
            edited,
            self.aider_edited_files,
            int(getattr(self, "num_reflections", 0) or 0),
        )

        # Lint before commit (do not commit yet — Z verify gate owns the commit point)
        if edited and self.auto_lint:
            lint_errors = self.lint_edited(edited)
            self.lint_outcome = not lint_errors
            if lint_errors:
                ok = self.io.confirm_ask("Attempt to fix lint errors?")
                if ok:
                    self.reflected_message = lint_errors
                    return

        shared_output = self.run_shell_commands()
        if shared_output:
            self.cur_messages += [
                dict(role="user", content=shared_output),
                dict(role="assistant", content="Ok"),
            ]

        # Z verify-before-commit gate: real tests + tiered uncertainty policy.
        # Replaces the old "commit then maybe auto-test then analyze" order.
        use_verify_gate = (
            bool(commit_edited)
            and not self.reflected_message
            and bool(getattr(self, "uncertainty_engine", None))
            and bool(getattr(self, "verify_commit_gate", True))
            and bool(self.auto_commits)
            and not self.dry_run
        )

        if use_verify_gate:
            from aider.z.uncertainty.gate import bind_acceptances_to_commit, prepare_commit
            from aider.z.uncertainty.verify import gate_enabled

            if gate_enabled():
                gate_result = prepare_commit(self, commit_edited)
                if gate_result.reflect_message:
                    self.reflected_message = gate_result.reflect_message
                    return
                if not gate_result.allow_commit:
                    from aider.z.uncertainty.gate import format_commit_blocked_message

                    detail = gate_result.reason or (
                        "Resolve high-risk issues, acknowledge medium-risk, "
                        "or use --force-commit / Z_FORCE_COMMIT."
                    )
                    if getattr(gate_result, "block_ui_emitted", False):
                        blocked_msg = (
                            gate_result.block_message
                            or format_commit_blocked_message(
                                detail,
                                dirty_count=len(commit_edited or []),
                            )
                        )
                    else:
                        blocked_msg = format_commit_blocked_message(
                            detail,
                            dirty_count=len(commit_edited or []),
                        )
                        self.io.tool_error(blocked_msg)
                    self.move_back_cur_messages(blocked_msg)
                    return

                saved_message = self.auto_commit(commit_edited)
                # Tie explicit acknowledgments / force overrides to the commit hash
                if saved_message and getattr(self, "last_aider_commit_hash", None):
                    accepted = list(gate_result.acknowledged_medium) + list(
                        gate_result.blocked_high if gate_result.force_override else []
                    )
                    if accepted and getattr(self, "uncertainty_store", None):
                        bind_acceptances_to_commit(
                            self.uncertainty_store,
                            {n.id for n in accepted},
                            self.last_aider_commit_hash,
                        )
                if not saved_message and hasattr(
                    self.gpt_prompts, "files_content_gpt_edits_no_repo"
                ):
                    saved_message = self.gpt_prompts.files_content_gpt_edits_no_repo
                self.move_back_cur_messages(saved_message)
                return

        # Legacy path (no Z uncertainty engine / gate disabled): commit, optional tests, analyze
        if commit_edited:
            saved_message = self.auto_commit(commit_edited)

            if not saved_message and hasattr(self.gpt_prompts, "files_content_gpt_edits_no_repo"):
                saved_message = self.gpt_prompts.files_content_gpt_edits_no_repo

            self.move_back_cur_messages(saved_message)

        if self.reflected_message:
            return

        if edited and self.auto_lint and not use_verify_gate:
            # Keep post-lint commit for legacy path only
            self.auto_commit(edited, context="Ran the linter")

        if edited and self.auto_test:
            test_errors = self.commands.cmd_test(self.test_cmd)
            self.test_outcome = not test_errors
            if test_errors:
                ok = self.io.confirm_ask("Attempt to fix test errors?")
                if ok:
                    self.reflected_message = test_errors
                    return

        # Uncertainty tree: run concrete detectors after edits settle (no reflection pending)
        if edited and not self.reflected_message:
            self._run_uncertainty_analysis(edited)

    def _run_uncertainty_analysis(self, edited):
        engine = getattr(self, "uncertainty_engine", None)
        if not engine or not edited:
            return
        try:
            # Capture model-listed edge cases / migration impact from the reply if present
            content = self.partial_response_content or ""
            self._ingest_uncertainty_self_reports(content)
            try:
                engine.record_discussed_text(content)
            except Exception:
                pass

            rels = []
            for path in edited:
                try:
                    rels.append(self.get_rel_fname(path))
                except Exception:
                    rels.append(str(path))

            # Diff scopes structural edge-case detection to changed lines
            try:
                repo = getattr(self, "repo", None)
                if repo is not None and hasattr(repo, "get_diffs"):
                    engine.record_diff(repo.get_diffs(fnames=list(edited)) or "")
            except Exception:
                pass

            new_nodes = engine.analyze_edits(
                rels,
                tests_passed=self.test_outcome,
            )
            if new_nodes:
                from aider.z.uncertainty.ui import print_summary_line

                print_summary_line(self.io, new_nodes)
                # Failing tests escalate — surface prominently
                failing = [
                    n
                    for n in new_nodes
                    if n.signals.get("tests_passed") is False
                    or n.status.value == "Needs Human Review"
                ]
                if failing and self.test_outcome is False:
                    self.io.tool_warning(
                        "Relevant tests failed — uncertainty tree escalated. "
                        "Use /uncertainties before proceeding silently."
                    )
        except Exception as err:
            if self.verbose:
                self.io.tool_warning(f"Uncertainty analysis skipped: {err}")

    def _ingest_uncertainty_self_reports(self, content: str):
        """Parse structured edge-case / migration-impact listings from the model reply."""
        engine = getattr(self, "uncertainty_engine", None)
        if not engine or not content:
            return
        import re

        # Edge cases block: lines after a heading the prompts may request
        edge_match = re.search(
            r"(?i)edge cases?\s+(?:considered\s+)?(?:but\s+)?(?:not\s+fully\s+handled)?\s*:?\s*\n((?:[-*].+\n?)+)",
            content,
        )
        if edge_match:
            cases = []
            for line in edge_match.group(1).splitlines():
                line = re.sub(r"^\s*[-*]\s*", "", line).strip()
                if line:
                    cases.append(line)
            if cases:
                engine.record_edge_cases(cases)

        mig = re.search(
            r"(?i)(?:migration\s+data\s+impact|existing\s+data\s+under\s+the\s+new\s+schema)\s*:?\s*(.+)",
            content,
        )
        if mig:
            engine.record_migration_impact(mig.group(1).strip()[:1000])

    def reply_completed(self):
        pass

    def show_exhausted_error(self):
        output_tokens = 0
        if self.partial_response_content:
            output_tokens = self.main_model.token_count(self.partial_response_content)
        max_output_tokens = self.main_model.info.get("max_output_tokens") or 0

        input_tokens = self.main_model.token_count(self.format_messages().all_messages())
        max_input_tokens = self.main_model.info.get("max_input_tokens") or 0

        total_tokens = input_tokens + output_tokens

        fudge = 0.7

        out_err = ""
        if output_tokens >= max_output_tokens * fudge:
            out_err = " -- possibly exceeded output limit!"

        inp_err = ""
        if input_tokens >= max_input_tokens * fudge:
            inp_err = " -- possibly exhausted context window!"

        tot_err = ""
        if total_tokens >= max_input_tokens * fudge:
            tot_err = " -- possibly exhausted context window!"

        res = ["", ""]
        res.append(f"Model {self.main_model.name} has hit a token limit!")
        res.append("Token counts below are approximate.")
        res.append("")
        res.append(f"Input tokens: ~{input_tokens:,} of {max_input_tokens:,}{inp_err}")
        res.append(f"Output tokens: ~{output_tokens:,} of {max_output_tokens:,}{out_err}")
        res.append(f"Total tokens: ~{total_tokens:,} of {max_input_tokens:,}{tot_err}")

        if output_tokens >= max_output_tokens:
            res.append("")
            res.append("To reduce output tokens:")
            res.append("- Ask for smaller changes in each request.")
            res.append("- Break your code into smaller source files.")
            if "diff" not in self.main_model.edit_format:
                res.append("- Use a stronger model that can return diffs.")

        if input_tokens >= max_input_tokens or total_tokens >= max_input_tokens:
            res.append("")
            res.append("To reduce input tokens:")
            res.append("- Use /tokens to see token usage.")
            res.append("- Use /drop to remove unneeded files from the chat session.")
            res.append("- Use /clear to clear the chat history.")
            res.append("- Break your code into smaller source files.")

        res = "".join([line + "\n" for line in res])
        self.io.tool_error(res)
        self.io.offer_url(urls.token_limits)

    def lint_edited(self, fnames):
        res = ""
        for fname in fnames:
            if not fname:
                continue
            errors = self.linter.lint(self.abs_root_path(fname))

            if errors:
                res += "\n"
                res += errors
                res += "\n"

        if res:
            self.io.tool_warning(res)

        return res

    def __del__(self):
        """Cleanup when the Coder object is destroyed."""
        self.ok_to_warm_cache = False

    def add_assistant_reply_to_cur_messages(self):
        if self.partial_response_content:
            self.cur_messages += [dict(role="assistant", content=self.partial_response_content)]
        if self.partial_response_function_call:
            self.cur_messages += [
                dict(
                    role="assistant",
                    content=None,
                    function_call=self.partial_response_function_call,
                )
            ]

    def get_file_mentions(self, content, ignore_current=False):
        words = set(word for word in content.split())

        # drop sentence punctuation from the end
        words = set(word.rstrip(",.!;:?") for word in words)

        # strip away all kinds of quotes
        quotes = "\"'`*_"
        words = set(word.strip(quotes) for word in words)

        if ignore_current:
            addable_rel_fnames = self.get_all_relative_files()
            existing_basenames = {}
        else:
            addable_rel_fnames = self.get_addable_relative_files()

            # Get basenames of files already in chat or read-only
            existing_basenames = {os.path.basename(f) for f in self.get_inchat_relative_files()} | {
                os.path.basename(self.get_rel_fname(f)) for f in self.abs_read_only_fnames
            }

        mentioned_rel_fnames = set()
        fname_to_rel_fnames = {}
        for rel_fname in addable_rel_fnames:
            normalized_rel_fname = rel_fname.replace("\\", "/")
            normalized_words = set(word.replace("\\", "/") for word in words)
            if normalized_rel_fname in normalized_words:
                mentioned_rel_fnames.add(rel_fname)

            fname = os.path.basename(rel_fname)

            # Don't add basenames that could be plain words like "run" or "make"
            if "/" in fname or "\\" in fname or "." in fname or "_" in fname or "-" in fname:
                if fname not in fname_to_rel_fnames:
                    fname_to_rel_fnames[fname] = []
                fname_to_rel_fnames[fname].append(rel_fname)

        for fname, rel_fnames in fname_to_rel_fnames.items():
            # If the basename is already in chat, don't add based on a basename mention
            if fname in existing_basenames:
                continue
            # If the basename mention is unique among addable files and present in the text
            if len(rel_fnames) == 1 and fname in words:
                mentioned_rel_fnames.add(rel_fnames[0])

        return mentioned_rel_fnames

    def check_for_file_mentions(self, content):
        mentioned_rel_fnames = self.get_file_mentions(content)

        new_mentions = mentioned_rel_fnames - self.ignore_mentions

        if not new_mentions:
            return

        # After plan approve / add-files miss: don't ask — just add.
        # Does NOT require --yes-always; plan Yes or miss prose is enough.
        auto_add = False
        try:
            from aider.z.ni_contract import detect_add_files_miss

            if detect_add_files_miss(content or ""):
                auto_add = True
            eng = getattr(self, "uncertainty_engine", None)
            if eng is not None and getattr(
                getattr(eng, "ctx", None), "plan_approved", False
            ):
                auto_add = True
        except Exception:
            pass
        # --yes-always still auto-adds ordinary mentions; not required for miss/plan
        if getattr(self.io, "yes", None) is True:
            auto_add = True

        added_fnames = []
        group = ConfirmGroup(new_mentions)
        for rel_fname in sorted(new_mentions):
            ok = auto_add or self.io.confirm_ask(
                "Add file to the chat?", subject=rel_fname, group=group, allow_never=True
            )
            if ok:
                self.add_rel_fname(rel_fname)
                added_fnames.append(rel_fname)
            else:
                self.ignore_mentions.add(rel_fname)

        if added_fnames:
            return prompts.added_files.format(fnames=", ".join(added_fnames))

    def send(self, messages, model=None, functions=None):
        self.got_reasoning_content = False
        self.ended_reasoning_content = False

        if not model:
            model = self.main_model

        self.partial_response_content = ""
        self.partial_response_function_call = dict()

        self.io.log_llm_history("TO LLM", format_messages(messages))

        completion = None
        try:
            hash_object, completion = model.send_completion(
                messages,
                functions,
                self.stream,
                self.temperature,
            )
            self.chat_completion_call_hashes.append(hash_object.hexdigest())

            if self.stream:
                yield from self.show_send_output_stream(completion)
            else:
                self.show_send_output(completion)

            # Calculate costs for successful responses
            self.calculate_and_show_tokens_and_cost(messages, completion)

        except LiteLLMExceptions().exceptions_tuple() as err:
            ex_info = LiteLLMExceptions().get_ex_info(err)
            if ex_info.name == "ContextWindowExceededError":
                # Still calculate costs for context window errors
                self.calculate_and_show_tokens_and_cost(messages, completion)
            raise
        except KeyboardInterrupt as kbi:
            self.keyboard_interrupt()
            raise kbi
        finally:
            self.io.log_llm_history(
                "LLM RESPONSE",
                format_content("ASSISTANT", self.partial_response_content),
            )

            if self.partial_response_content:
                self.io.ai_output(self.partial_response_content)
            elif self.partial_response_function_call:
                # TODO: push this into subclasses
                args = self.parse_partial_args()
                if args:
                    self.io.ai_output(json.dumps(args, indent=4))

    def show_send_output(self, completion):
        # Stop spinner once we have a response
        self._stop_waiting_spinner()

        if self.verbose:
            print(completion)

        if not completion.choices:
            self.io.tool_error(str(completion))
            return

        show_func_err = None
        show_content_err = None
        try:
            if completion.choices[0].message.tool_calls:
                self.partial_response_function_call = (
                    completion.choices[0].message.tool_calls[0].function
                )
        except AttributeError as func_err:
            show_func_err = func_err

        try:
            reasoning_content = completion.choices[0].message.reasoning_content
        except AttributeError:
            try:
                reasoning_content = completion.choices[0].message.reasoning
            except AttributeError:
                reasoning_content = None

        try:
            self.partial_response_content = completion.choices[0].message.content or ""
        except AttributeError as content_err:
            show_content_err = content_err

        resp_hash = dict(
            function_call=str(self.partial_response_function_call),
            content=self.partial_response_content,
        )
        resp_hash = hashlib.sha1(json.dumps(resp_hash, sort_keys=True).encode())
        self.chat_completion_response_hashes.append(resp_hash.hexdigest())

        if show_func_err and show_content_err:
            self.io.tool_error(show_func_err)
            self.io.tool_error(show_content_err)
            raise Exception("No data found in LLM response!")

        show_resp = self.render_incremental_response(True)

        if reasoning_content:
            formatted_reasoning = format_reasoning_content(
                reasoning_content, self.reasoning_tag_name
            )
            show_resp = formatted_reasoning + show_resp

        show_resp = replace_reasoning_tags(show_resp, self.reasoning_tag_name)

        self.io.assistant_output(show_resp, pretty=self.show_pretty())

        if (
            hasattr(completion.choices[0], "finish_reason")
            and completion.choices[0].finish_reason == "length"
        ):
            raise FinishReasonLength()

    def show_send_output_stream(self, completion):
        received_content = False

        for chunk in completion:
            if len(chunk.choices) == 0:
                continue

            if (
                hasattr(chunk.choices[0], "finish_reason")
                and chunk.choices[0].finish_reason == "length"
            ):
                raise FinishReasonLength()

            try:
                func = chunk.choices[0].delta.function_call
                # dump(func)
                for k, v in func.items():
                    if k in self.partial_response_function_call:
                        self.partial_response_function_call[k] += v
                    else:
                        self.partial_response_function_call[k] = v
                received_content = True
            except AttributeError:
                pass

            text = ""

            try:
                reasoning_content = chunk.choices[0].delta.reasoning_content
            except AttributeError:
                try:
                    reasoning_content = chunk.choices[0].delta.reasoning
                except AttributeError:
                    reasoning_content = None

            if reasoning_content:
                if not self.got_reasoning_content:
                    text += f"<{REASONING_TAG}>\n\n"
                text += reasoning_content
                self.got_reasoning_content = True
                received_content = True

            try:
                content = chunk.choices[0].delta.content
                if content:
                    if self.got_reasoning_content and not self.ended_reasoning_content:
                        text += f"\n\n</{self.reasoning_tag_name}>\n\n"
                        self.ended_reasoning_content = True

                    text += content
                    received_content = True
            except AttributeError:
                pass

            if received_content:
                self._stop_waiting_spinner()
            self.partial_response_content += text

            if self.show_pretty():
                self.live_incremental_response(False)
            elif text:
                # Apply reasoning tag formatting
                text = replace_reasoning_tags(text, self.reasoning_tag_name)
                try:
                    sys.stdout.write(text)
                except UnicodeEncodeError:
                    # Safely encode and decode the text
                    safe_text = text.encode(sys.stdout.encoding, errors="backslashreplace").decode(
                        sys.stdout.encoding
                    )
                    sys.stdout.write(safe_text)
                sys.stdout.flush()
                yield text

        if not received_content:
            self.io.tool_warning("Empty response received from LLM. Check your provider account?")

    def live_incremental_response(self, final):
        show_resp = self.render_incremental_response(final)
        # Apply any reasoning tag formatting
        show_resp = replace_reasoning_tags(show_resp, self.reasoning_tag_name)
        self.mdstream.update(show_resp, final=final)

    def render_incremental_response(self, final):
        return self.get_multi_response_content_in_progress()

    def remove_reasoning_content(self):
        """Remove reasoning content from the model's response."""

        self.partial_response_content = remove_reasoning_content(
            self.partial_response_content,
            self.reasoning_tag_name,
        )

    def calculate_and_show_tokens_and_cost(self, messages, completion=None):
        prompt_tokens = 0
        completion_tokens = 0
        cache_hit_tokens = 0
        cache_write_tokens = 0

        if completion and hasattr(completion, "usage") and completion.usage is not None:
            prompt_tokens = completion.usage.prompt_tokens
            completion_tokens = completion.usage.completion_tokens
            cache_hit_tokens = getattr(completion.usage, "prompt_cache_hit_tokens", 0) or getattr(
                completion.usage, "cache_read_input_tokens", 0
            )
            cache_write_tokens = getattr(completion.usage, "cache_creation_input_tokens", 0)

            if hasattr(completion.usage, "cache_read_input_tokens") or hasattr(
                completion.usage, "cache_creation_input_tokens"
            ):
                self.message_tokens_sent += prompt_tokens
                self.message_tokens_sent += cache_write_tokens
            else:
                self.message_tokens_sent += prompt_tokens

        else:
            prompt_tokens = self.main_model.token_count(messages)
            completion_tokens = self.main_model.token_count(self.partial_response_content)
            self.message_tokens_sent += prompt_tokens

        self.message_tokens_received += completion_tokens

        tokens_report = f"Tokens: {format_tokens(self.message_tokens_sent)} sent"

        if cache_write_tokens:
            tokens_report += f", {format_tokens(cache_write_tokens)} cache write"
        if cache_hit_tokens:
            tokens_report += f", {format_tokens(cache_hit_tokens)} cache hit"
        tokens_report += f", {format_tokens(self.message_tokens_received)} received."

        if not self.main_model.info.get("input_cost_per_token"):
            self.usage_report = tokens_report
            return

        try:
            # Try and use litellm's built in cost calculator. Seems to work for non-streaming only?
            cost = litellm.completion_cost(completion_response=completion)
        except Exception:
            cost = 0

        if not cost:
            cost = self.compute_costs_from_tokens(
                prompt_tokens, completion_tokens, cache_write_tokens, cache_hit_tokens
            )

        self.total_cost += cost
        self.message_cost += cost

        def format_cost(value):
            if value == 0:
                return "0.00"
            magnitude = abs(value)
            if magnitude >= 0.01:
                return f"{value:.2f}"
            else:
                return f"{value:.{max(2, 2 - int(math.log10(magnitude)))}f}"

        cost_report = (
            f"Cost: ${format_cost(self.message_cost)} message,"
            f" ${format_cost(self.total_cost)} session."
        )

        if cache_hit_tokens and cache_write_tokens:
            sep = "\n"
        else:
            sep = " "

        self.usage_report = tokens_report + sep + cost_report

    def compute_costs_from_tokens(
        self, prompt_tokens, completion_tokens, cache_write_tokens, cache_hit_tokens
    ):
        cost = 0

        input_cost_per_token = self.main_model.info.get("input_cost_per_token") or 0
        output_cost_per_token = self.main_model.info.get("output_cost_per_token") or 0
        input_cost_per_token_cache_hit = (
            self.main_model.info.get("input_cost_per_token_cache_hit") or 0
        )

        # deepseek
        # prompt_cache_hit_tokens + prompt_cache_miss_tokens
        #    == prompt_tokens == total tokens that were sent
        #
        # Anthropic
        # cache_creation_input_tokens + cache_read_input_tokens + prompt
        #    == total tokens that were

        if input_cost_per_token_cache_hit:
            # must be deepseek
            cost += input_cost_per_token_cache_hit * cache_hit_tokens
            cost += (prompt_tokens - input_cost_per_token_cache_hit) * input_cost_per_token
        else:
            # hard code the anthropic adjustments, no-ops for other models since cache_x_tokens==0
            cost += cache_write_tokens * input_cost_per_token * 1.25
            cost += cache_hit_tokens * input_cost_per_token * 0.10
            cost += prompt_tokens * input_cost_per_token

        cost += completion_tokens * output_cost_per_token
        return cost

    def show_usage_report(self):
        if not self.usage_report:
            return

        self.total_tokens_sent += self.message_tokens_sent
        self.total_tokens_received += self.message_tokens_received

        from aider.z.ux_flags import show_usage_enabled

        if show_usage_enabled(coder=self, io=self.io):
            self.io.tool_output(self.usage_report)

        prompt_tokens = self.message_tokens_sent
        completion_tokens = self.message_tokens_received
        self.event(
            "message_send",
            main_model=self.main_model,
            edit_format=self.edit_format,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            cost=self.message_cost,
            total_cost=self.total_cost,
        )

        self.message_cost = 0.0
        self.message_tokens_sent = 0
        self.message_tokens_received = 0

    def get_multi_response_content_in_progress(self, final=False):
        cur = self.multi_response_content or ""
        new = self.partial_response_content or ""

        if new.rstrip() != new and not final:
            new = new.rstrip()

        return cur + new

    def get_rel_fname(self, fname):
        try:
            return os.path.relpath(fname, self.root)
        except ValueError:
            return fname

    def get_inchat_relative_files(self):
        files = [self.get_rel_fname(fname) for fname in self.abs_fnames]
        return sorted(set(files))

    def is_file_safe(self, fname):
        try:
            return Path(self.abs_root_path(fname)).is_file()
        except OSError:
            return

    def get_all_relative_files(self):
        if self.repo:
            files = self.repo.get_tracked_files()
        else:
            files = self.get_inchat_relative_files()

        # This is quite slow in large repos
        # files = [fname for fname in files if self.is_file_safe(fname)]

        return sorted(set(files))

    def get_all_abs_files(self):
        files = self.get_all_relative_files()
        files = [self.abs_root_path(path) for path in files]
        return files

    def get_addable_relative_files(self):
        all_files = set(self.get_all_relative_files())
        inchat_files = set(self.get_inchat_relative_files())
        read_only_files = set(self.get_rel_fname(fname) for fname in self.abs_read_only_fnames)
        return all_files - inchat_files - read_only_files

    def check_for_dirty_commit(self, path):
        if not self.repo:
            return
        if not self.dirty_commits:
            return
        # Hold dirty-commits while verify-gate reflect/recovery is in progress so
        # the initial broken implementation is not committed before fixes land.
        if getattr(self, "_z_gate_hold_dirty", False):
            return
        if int(getattr(self, "_z_verify_gen_attempts", 0) or 0) > 0:
            return
        if int(getattr(self, "_z_verify_fix_attempts", 0) or 0) > 0:
            return
        if os.environ.get("Z_NO_DIRTY_COMMIT", "").strip().lower() in ("1", "true", "yes"):
            return
        if not self.repo.is_dirty(path):
            return

        # We need a committed copy of the file in order to /undo, so skip this
        # fullp = Path(self.abs_root_path(path))
        # if not fullp.stat().st_size:
        #     return

        self.io.tool_output(f"Committing {path} before applying edits.")
        self.need_commit_before_edits.add(path)

    def allowed_to_edit(self, path):
        # Plan permission mode first — plan artifacts may live under $Z_HOME
        # (outside the git root), so check before path_under_root.
        try:
            from aider.z.plan_mode import is_plan_artifact_path, plans_dir
            from aider.z.task_mode import TaskMode

            mode = getattr(self, "task_mode", None)
            if mode is TaskMode.PLAN:
                raw = str(path)
                if is_plan_artifact_path(raw, root=getattr(self, "root", None)):
                    full_plan = str(Path(raw).expanduser().resolve())
                    if not Path(full_plan).exists():
                        Path(full_plan).parent.mkdir(parents=True, exist_ok=True)
                        if not self.dry_run:
                            utils.touch_file(full_plan)
                    self.abs_fnames.add(full_plan)
                    return True
                # Relative plan names → write under plans_dir()
                if "/" not in raw.replace("\\", "/") and raw.endswith(".md"):
                    full_plan = str((plans_dir() / raw).resolve())
                    Path(full_plan).parent.mkdir(parents=True, exist_ok=True)
                    if not Path(full_plan).exists() and not self.dry_run:
                        utils.touch_file(full_plan)
                    self.abs_fnames.add(full_plan)
                    return True
                msg = (
                    f"Plan mode: blocked product edit to `{path}`. "
                    "Write the plan artifact only, then `/plan-exit` to implement."
                )
                self.io.tool_error(msg)
                prev = getattr(self, "reflected_message", None) or ""
                self.reflected_message = (prev + "\n" + msg).strip() if prev else msg
                return
        except Exception:
            pass

        full_path = self.path_under_root(path)
        if full_path is None:
            self.io.tool_warning(
                f"Skipping edits outside project root: {path}"
            )
            return

        if self.repo:
            need_to_add = not self.repo.path_in_repo(path)
        else:
            need_to_add = False

        if full_path in self.abs_fnames:
            self.check_for_dirty_commit(path)
            return True

        if self.repo and self.repo.git_ignored_file(path):
            self.io.tool_warning(f"Skipping edits to {path} that matches gitignore spec.")
            return

        if not Path(full_path).exists():
            # Mechanical block: do not create top-level packages that shadow
            # declared / recently-missing third-party dependencies (freezegun case).
            if self._blocks_dependency_fabrication(path, full_path):
                return

            if not self.io.confirm_ask("Create new file?", subject=path):
                self.io.tool_output(f"Skipping edits to {path}")
                return

            if not self.dry_run:
                if not utils.touch_file(full_path):
                    self.io.tool_error(f"Unable to create {path}, skipping edits.")
                    return

                # Seems unlikely that we needed to create the file, but it was
                # actually already part of the repo.
                # But let's only add if we need to, just to be safe.
                if need_to_add and self.auto_commits:
                    self.repo.repo.git.add(full_path)

            self.abs_fnames.add(full_path)
            self.check_added_files()
            return True

        # Existing file not in chat — OpenCode-style read-before-edit.
        # Default strict mode refuses edits (even under --yes-always) so the
        # model cannot invent patches for unread files. Legacy confirm remains
        # behind Z_STRICT_CHAT_EDITS=0.
        from aider.z.coding_context import strict_chat_edits_enabled

        if strict_chat_edits_enabled():
            msg = (
                f"Edit blocked: `{path}` is not in the chat. "
                f"Add it first (`/add {path}`) so its contents are visible, "
                "then propose SEARCH/REPLACE. New files may still be created."
            )
            self.io.tool_error(msg)
            # Feed a reflect so the model can ask to add the file next turn
            prev = getattr(self, "reflected_message", None) or ""
            self.reflected_message = (prev + "\n" + msg).strip() if prev else msg
            return

        if not self.io.confirm_ask(
            "Allow edits to file that has not been added to the chat?",
            subject=path,
        ):
            self.io.tool_output(f"Skipping edits to {path}")
            return

        if need_to_add and self.auto_commits:
            self.repo.repo.git.add(full_path)

        self.abs_fnames.add(full_path)
        self.check_added_files()
        self.check_for_dirty_commit(path)

        return True

    warning_given = False

    def check_added_files(self):
        if self.warning_given:
            return

        warn_number_of_files = 4
        warn_number_of_tokens = 20 * 1024

        num_files = len(self.abs_fnames)
        if num_files < warn_number_of_files:
            return

        tokens = 0
        for fname in self.abs_fnames:
            if is_image_file(fname):
                continue
            content = self.io.read_text(fname)
            tokens += self.main_model.token_count(content)

        if tokens < warn_number_of_tokens:
            return

        self.io.tool_warning("Warning: it's best to only add files that need changes to the chat.")
        self.io.tool_warning(urls.edit_errors)
        self.warning_given = True

    def prepare_to_edit(self, edits):
        res = []
        seen = dict()

        self.need_commit_before_edits = set()

        for edit in edits:
            path = edit[0]
            if path is None:
                res.append(edit)
                continue
            if path == "python":
                dump(edits)
            if path in seen:
                allowed = seen[path]
            else:
                allowed = self.allowed_to_edit(path)
                seen[path] = allowed

            if allowed:
                res.append(edit)

        self.dirty_commit()
        self.need_commit_before_edits = set()

        return res

    def apply_updates(self):
        edited = set()
        # Hard stop: high-stakes plan required but not approved → no diffs
        engine = getattr(self, "uncertainty_engine", None)
        if engine is not None:
            try:
                if engine.edits_blocked_pending_plan():
                    msg = (
                        "Edits blocked: a high-stakes implementation plan is required "
                        "and has not been approved yet."
                    )
                    self.io.tool_error(msg)
                    self.reflected_message = msg
                    return edited
            except Exception:
                pass

        content = getattr(self, "partial_response_content", None) or ""
        proposed_edit_blocks = self._response_has_edit_blocks(content)

        try:
            edits = self.get_edits()
            edits_before_filter = list(edits or [])
            edits = self.apply_edits_dry_run(edits)
            edits = self.prepare_to_edit(edits)
            # Eval Finding 2: never silently drop a full SEARCH/REPLACE stream
            if edits_before_filter and not edits:
                msg = (
                    "FAILED TO APPLY EDIT: model proposed SEARCH/REPLACE block(s) "
                    "but every edit was blocked or skipped (not in chat / create "
                    "refused / outside repo). Nothing was written to disk."
                )
                self.io.tool_error(msg)
                self.reflected_message = msg
                self._z_edit_apply_failed = True
                return edited

            edited = set(edit[0] for edit in edits)

            self.apply_edits(edits)
        except ValueError as err:
            self.num_malformed_responses += 1

            err = err.args[0]

            self.io.tool_error("The LLM did not conform to the edit format.")
            self.io.tool_output(urls.edit_errors)
            self.io.tool_output()
            self.io.tool_output(str(err))

            self.reflected_message = str(err)
            self._z_edit_apply_failed = True
            return edited

        except ANY_GIT_ERROR as err:
            self.io.tool_error(str(err))
            return edited
        except Exception as err:
            self.io.tool_error("Exception while updating files:")
            self.io.tool_error(str(err), strip=False)

            traceback.print_exc()

            self.reflected_message = str(err)
            self._z_edit_apply_failed = True
            return edited

        for path in edited:
            if self.dry_run:
                self.io.tool_output(f"Did not apply edit to {path} (--dry-run)")
            else:
                self.io.tool_output(f"Applied edit to {path}")

        # Eval Finding 2: fences in the reply but zero applied paths
        if proposed_edit_blocks and not edited and not self.dry_run:
            msg = (
                "FAILED TO APPLY EDIT: response contained SEARCH/REPLACE fences "
                "but no file was updated on disk. Refusing silent success."
            )
            self.io.tool_error(msg)
            if not getattr(self, "reflected_message", None):
                self.reflected_message = msg
            self._z_edit_apply_failed = True

        return edited

    @staticmethod
    def _response_has_edit_blocks(content: str) -> bool:
        if not content:
            return False
        has_search = bool(re.search(r"^<{5,9}\s*SEARCH", content, re.M | re.I))
        has_div = "=======" in content
        has_rep = bool(re.search(r"^>{5,9}\s*REPLACE", content, re.M | re.I))
        return has_search and has_div and has_rep

    def parse_partial_args(self):
        # dump(self.partial_response_function_call)

        data = self.partial_response_function_call.get("arguments")
        if not data:
            return

        try:
            return json.loads(data)
        except JSONDecodeError:
            pass

        try:
            return json.loads(data + "]}")
        except JSONDecodeError:
            pass

        try:
            return json.loads(data + "}]}")
        except JSONDecodeError:
            pass

        try:
            return json.loads(data + '"}]}')
        except JSONDecodeError:
            pass

    # commits...

    def get_context_from_history(self, history):
        context = ""
        if history:
            for msg in history:
                context += "\n" + msg["role"].upper() + ": " + msg["content"] + "\n"

        return context

    def auto_commit(self, edited, context=None):
        if not self.repo or not self.auto_commits or self.dry_run:
            return

        if not context:
            context = self.get_context_from_history(self.cur_messages)

        try:
            res = self.repo.commit(fnames=edited, context=context, aider_edits=True, coder=self)
            if res:
                self.show_auto_commit_outcome(res)
                commit_hash, commit_message = res
                return self.gpt_prompts.files_content_gpt_edits.format(
                    hash=commit_hash,
                    message=commit_message,
                )

            return self.gpt_prompts.files_content_gpt_no_edits
        except ANY_GIT_ERROR as err:
            self.io.tool_error(f"Unable to commit: {str(err)}")
            return

    def show_auto_commit_outcome(self, res):
        commit_hash, commit_message = res
        self.last_aider_commit_hash = commit_hash
        self.aider_commit_hashes.add(commit_hash)
        self.last_aider_commit_message = commit_message
        if self.show_diffs:
            self.commands.cmd_diff()

    def show_undo_hint(self):
        if not self.commit_before_message:
            return
        if self.commit_before_message[-1] != self.repo.get_head_commit_sha():
            self.io.tool_output("You can use /undo to undo and discard each aider commit.")

    def dirty_commit(self):
        if not self.need_commit_before_edits:
            return
        if not self.dirty_commits:
            return
        if not self.repo:
            return

        # Same bookkeeping as auto_commit(): lint-fix / multi-reflection loops
        # often land real work only via dirty_commit(). If we discard the result,
        # last_aider_commit_hash stays None and exhaustion-path skill capture
        # (and other hash consumers) miss commits that already exist in git.
        try:
            res = self.repo.commit(fnames=self.need_commit_before_edits, coder=self)
        except ANY_GIT_ERROR as err:
            self.io.tool_error(f"Unable to commit: {str(err)}")
            return
        if res:
            self.show_auto_commit_outcome(res)

        # files changed, move cur messages back behind the files messages
        # self.move_back_cur_messages(self.gpt_prompts.files_content_local_edits)
        return True

    def get_edits(self, mode="update"):
        return []

    def apply_edits(self, edits):
        return

    def apply_edits_dry_run(self, edits):
        return edits

    def run_shell_commands(self):
        if not self.suggest_shell_commands:
            return ""

        done = set()
        group = ConfirmGroup(set(self.shell_commands))
        accumulated_output = ""
        for command in self.shell_commands:
            if command in done:
                continue
            done.add(command)
            output = self.handle_shell_commands(command, group)
            if output:
                accumulated_output += output + "\n\n"
        return accumulated_output

    def handle_shell_commands(self, commands_str, group):
        commands = commands_str.strip().splitlines()
        command_count = sum(
            1 for cmd in commands if cmd.strip() and not cmd.strip().startswith("#")
        )
        prompt = "Run shell command?" if command_count == 1 else "Run shell commands?"
        skipped = "\n".join(
            c for c in commands if c.strip() and not c.strip().startswith("#")
        )

        # P0.5 — risk-class policy (structural argv parse). Does NOT weaken
        # --yes-always for arbitrary commands; only auto-approves narrow classes.
        from aider.z.shell_risk import (
            CommandRiskClass,
            classify_command,
            policy_ask_once_per_class,
            policy_auto_approves,
        )

        root = Path(getattr(self, "root", None) or ".")
        real_commands = [
            c.strip() for c in commands if c.strip() and not c.strip().startswith("#")
        ]
        classified = [classify_command(c, root=root) for c in real_commands]
        auto_approved = False
        pending_token = None

        if classified and all(policy_auto_approves(c.risk_class) for c in classified):
            auto_approved = True
            for c in classified:
                if c.notice:
                    self.io.tool_output(c.notice)
        else:
            # Session-scoped ask-once for local mutation classes
            if self._shell_class_approvals is None:
                self._shell_class_approvals = {}
            if (
                classified
                and all(policy_ask_once_per_class(c.risk_class) for c in classified)
                and all(
                    self._shell_class_approvals.get(c.risk_class.value) for c in classified
                )
            ):
                auto_approved = True
            else:
                # Bind approval to exact pending command token (P0.5)
                pending_token = (
                    classified[0].approval_token if len(classified) == 1 else None
                )
                subject = "\n".join(commands)
                if pending_token:
                    subject = f"{subject}\n\n[approval-token: {pending_token}]"
                approved = self.io.confirm_ask(
                    prompt,
                    subject=subject,
                    explicit_yes_required=True,
                    group=group,
                    allow_never=True,
                )
                if not approved:
                    msg = (
                        f"blocked: needs human approval to run:\n{skipped}\n"
                        "Read-only repo commands, declared project checks "
                        "(including cmake --build / ctest when CMakeLists.txt "
                        "exists), and declared dependency restores are "
                        "auto-approved; all other shell commands require an "
                        "interactive Yes.\n"
                        "If verification later says TESTS_FAILED / Not Run, "
                        "the build step may never have been allowed to run — "
                        "not necessarily that the code is broken."
                    )
                    self.io.tool_error(msg)
                    engine = getattr(self, "uncertainty_engine", None)
                    if engine is not None:
                        try:
                            engine.record_execution(msg)
                            self._emit_shell_approval_block_node(skipped)
                        except Exception:
                            pass
                    return
                # Remember local-mutation class approvals for this session
                for c in classified:
                    if policy_ask_once_per_class(c.risk_class):
                        self._shell_class_approvals[c.risk_class.value] = True
                # Token check: if a single command was shown, the subject carried
                # the token; free-form yes is accepted only for that confirm_ask.
                if pending_token and classified:
                    # confirm_ask already returned True for this exact prompt;
                    # stash token as satisfied for audit
                    self._last_shell_approval_token = pending_token

        accumulated_output = ""
        for command in commands:
            command = command.strip()
            if not command or command.startswith("#"):
                continue

            self.io.tool_output()
            self.io.tool_output(f"Running {command}")
            # Add the command to input history
            self.io.add_to_input_history(f"/run {command.strip()}")
            # Investigation evidence: grep/rg/search counts as inspecting named areas
            try:
                eng = getattr(self, "uncertainty_engine", None)
                if eng is not None and hasattr(eng, "record_execution"):
                    cl = command.lower()
                    if re.search(
                        r"(?:^|[;&|]\s*)(?:rg|grep|ag|ack|git\s+grep|findstr)\b",
                        cl,
                    ) or re.search(r"\bgrep\b|\brg\b", cl):
                        eng.record_execution(f"grep: {command.strip()[:240]}")
            except Exception:
                pass
            exit_status, output = run_cmd(command, error_print=self.io.tool_error, cwd=self.root)
            if output:
                accumulated_output += f"Output from {command}\n{output}\n"
            # P1.2 — record command evidence for shell-approval auto-resolution
            try:
                eng = getattr(self, "uncertainty_engine", None)
                if eng is not None and hasattr(eng, "record_execution"):
                    if exit_status == 0:
                        eng.record_execution(f"command_ok: {command.strip()[:240]}")
                        eng.record_execution(
                            f"command_success:{command.strip()[:240]}"
                        )
                        self._maybe_auto_resolve_shell_nodes(command.strip())
                    else:
                        eng.record_execution(f"command_failed:{command.strip()[:240]}")
            except Exception:
                pass

        # Auto-approved declared installs/checks: always fold output into chat
        add_output = bool(accumulated_output.strip()) and (
            auto_approved
            or self.io.confirm_ask("Add command output to the chat?", allow_never=True)
        )
        if add_output and accumulated_output.strip():
            # OpenCode-style tool-output budget: persist fat dumps, keep a preview
            try:
                from aider.z.output_budget import budget_tool_output

                budgeted, saved = budget_tool_output(
                    accumulated_output, label="shell"
                )
                if saved is not None:
                    self.io.tool_output(
                        f"Command output budgeted → {saved} "
                        "(preview added to chat)."
                    )
                accumulated_output = budgeted
            except Exception:
                pass
            num_lines = len(accumulated_output.strip().splitlines())
            line_plural = "line" if num_lines == 1 else "lines"
            self.io.tool_output(f"Added {num_lines} {line_plural} of output to the chat.")
            return accumulated_output

    def _emit_shell_approval_block_node(self, skipped_commands: str) -> None:
        """Surface a blocked shell confirm as a visible uncertainty finding."""
        engine = getattr(self, "uncertainty_engine", None)
        if engine is None:
            return
        from aider.z.uncertainty.detectors import _make_node
        from aider.z.uncertainty.risk import collect_base_signals
        from aider.z.uncertainty.schema import NodeStatus, NodeType, Tier

        sig = collect_base_signals([])
        node = _make_node(
            title="Shell command needs human approval",
            node_type=NodeType.FAILURE_BLIND_SPOT,
            signals=sig,
            summary="blocked: needs human approval to run a shell command.",
            explanation=(
                "A suggested shell command could not be auto-approved. Only "
                "installs of packages already declared in the project manifest "
                "are auto-approved; everything else requires an interactive Yes.\n"
                f"Command(s):\n{skipped_commands}"
            ),
            why_uncertain="Non-interactive run cannot answer shell confirmation.",
            what_could_go_wrong=(
                "The task may look finished while a required install/command never ran."
            ),
            suggested_fix=(
                "Re-run interactively and approve the command, or add the package "
                "to the project manifest if it should be auto-installable."
            ),
            suggested_prompt=(
                f"blocked: needs human approval to run:\n{skipped_commands}"
            ),
            status=NodeStatus.NEEDS_HUMAN_REVIEW,
            extra_signals={
                "shell_approval_blocked": True,
                "shell_approval_block": True,
                "temporary_blocker": True,
                "from_shell": True,
                "blocked_commands": skipped_commands,
                "blocked_command": (skipped_commands or "").strip().splitlines()[0]
                if skipped_commands
                else "",
                "lifecycle": "temporary_blocker",
                "expires_after_task": True,
            },
        )
        node.risk_tier = Tier.MEDIUM
        node.confidence_tier = Tier.LOW
        if getattr(engine.ctx, "current_task_id", None):
            node.task_id = engine.ctx.current_task_id
        engine.store.add(node)

    def _maybe_auto_resolve_shell_nodes(self, command: str) -> None:
        """P1.2: resolve shell-approval nodes when the exact command later succeeds."""
        engine = getattr(self, "uncertainty_engine", None)
        if engine is None or not command:
            return
        from aider.z.uncertainty.resolution import try_auto_resolve
        from aider.z.uncertainty.schema import NodeStatus

        evidence = []
        if hasattr(engine.ctx, "execution_log"):
            evidence = [
                line.strip()
                for line in (engine.ctx.execution_log or "").splitlines()
                if line.strip()
            ]
        evidence.append(f"command_ok: {command}")
        evidence.append(f"command_success:{command}")
        for node in list(engine.store.list(include_resolved=False)):
            signals = dict(node.signals or {})
            if not (
                signals.get("shell_approval_block")
                or signals.get("shell_approval_blocked")
            ):
                continue
            blocked = (
                signals.get("blocked_command")
                or (signals.get("blocked_commands") or "").splitlines()[0:1]
            )
            if isinstance(blocked, list):
                blocked = blocked[0] if blocked else ""
            if blocked and blocked.strip() not in command and command not in blocked:
                continue
            if try_auto_resolve(node, session_evidence=evidence):
                engine.store.update_status(node.id, NodeStatus.RESOLVED)
