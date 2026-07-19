"""Extract a JSON object from an LLM response that may include prose.

Models often wrap JSON in fences or add a short preamble/postamble. Naive
``find("{")`` / ``rfind("}")`` (or a greedy ``{...}`` regex) mis-bounds the
payload whenever incidental braces appear in that prose — e.g. ``\\d{1,3}``,
``std::lock_guard{mtx}``, bash ``${VAR}``. Callers then misattribute the
failure to "model non-compliance" and burn a retry.

Use ``extract_json_from_response`` anywhere Z must pull a JSON object out of
model text.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional


def extract_json_from_response(text: str) -> Optional[dict]:
    """Return the first valid JSON *object* found in *text*, or None."""
    if not text:
        return None
    text = text.strip()

    # Anchored: the entire response is a single fenced block.
    fence = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", text)
    if fence:
        text = fence.group(1).strip()
    parsed = _loads_object(text)
    if parsed is not None:
        return parsed

    # Non-anchored: a fenced block anywhere (prose before/after the fence).
    fence_search = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if fence_search:
        parsed = _loads_object(fence_search.group(1).strip())
        if parsed is not None:
            return parsed

    # Try every '{' as a candidate start, walking bracket depth while
    # respecting string literals — so braces inside quoted values (and
    # incidental braces in surrounding prose) are not miscounted.
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        depth = 0
        in_string = False
        escape = False
        for j in range(i, len(text)):
            c = text[j]
            if in_string:
                if escape:
                    escape = False
                elif c == "\\":
                    escape = True
                elif c == '"':
                    in_string = False
                continue
            if c == '"':
                in_string = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    parsed = _loads_object(text[i : j + 1])
                    if parsed is not None:
                        return parsed
                    break  # this '{' wasn't a valid JSON start; try next
    return None


def _loads_object(blob: str) -> Optional[dict]:
    if not blob:
        return None
    try:
        data: Any = json.loads(blob)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
