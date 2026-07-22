"""
Gateway usage aggregate client for Profile (Phase 9 + 14 honesty).

Proxies authenticated ``GET /v1/gateway/usage?range=billing_period|all``
and normalizes the response for the desktop Profile panel.

Phase 14: unsigned-in and gateway errors return empty series — never fake spend
unless ``Z_GATEWAY_USAGE_STUB`` is explicitly set (tests/dev).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from aider.z.auth import get_auth_base_url
from aider.z.credentials import load_credentials


ALLOWED_RANGES = frozenset({"billing_period", "all"})


def _empty_summary(
    range_key: str,
    *,
    authenticated: bool,
    note: str | None = None,
    error: str | None = None,
    source: str = "empty",
) -> dict[str, Any]:
    return {
        "range": range_key,
        "by_model": [],
        "total_requests": 0,
        "total_cost_usd": 0.0,
        "source": source,
        "authenticated": authenticated,
        "note": note,
        "error": error,
    }


def _stub_summary(range_key: str) -> dict[str, Any]:
    raw = os.environ.get("Z_GATEWAY_USAGE_STUB", "").strip()
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                data.setdefault("range", range_key)
                data["source"] = "stub"
                data["authenticated"] = True
                return data
        except json.JSONDecodeError:
            pass
    return {
        "range": range_key,
        "by_model": [
            {
                "model_id": "z-composer",
                "requests": 12,
                "input_tokens": 48000,
                "output_tokens": 12000,
                "cost_usd": 1.24,
            },
            {
                "model_id": "z-sonnet",
                "requests": 4,
                "input_tokens": 22000,
                "output_tokens": 8000,
                "cost_usd": 0.86,
            },
        ],
        "total_requests": 16,
        "total_cost_usd": 2.10,
        "source": "stub",
        "authenticated": True,
    }


def fetch_usage_summary(
    range_key: str = "billing_period",
    *,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Fetch usage aggregate for Profile (honest empty when unsigned-in)."""
    key = (range_key or "billing_period").strip().lower()
    if key not in ALLOWED_RANGES:
        if key in {"today", "7d", "30d", "month"}:
            key = "billing_period"
        else:
            key = "billing_period"

    # Explicit stub only — do not treat Z_GATEWAY_STUB as usage demo (Phase 14).
    if os.environ.get("Z_GATEWAY_USAGE_STUB"):
        out = _stub_summary(key)
        out["range"] = key
        return out

    creds = load_credentials()
    if creds is None or not getattr(creds, "access_token", None):
        return _empty_summary(
            key,
            authenticated=False,
            note="Sign in to see live gateway usage.",
            source="unsigned",
        )

    url = f"{get_auth_base_url()}/v1/gateway/usage?{urllib.parse.urlencode({'range': key})}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {creds.access_token}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError("usage response is not an object")
        data.setdefault("range", key)
        data["source"] = "gateway"
        data["authenticated"] = True
        return data
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        TimeoutError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        return _empty_summary(
            key,
            authenticated=True,
            error=f"Could not reach gateway usage ({exc}).",
            source="error",
        )


def normalize_for_profile(payload: dict[str, Any]) -> dict[str, Any]:
    """Shape gateway payload for the Profile webview (totals + byModel)."""
    by_model_raw = payload.get("by_model")
    if by_model_raw is None:
        by_model_raw = payload.get("byModel")
    if not isinstance(by_model_raw, list):
        by_model_raw = []

    by_model: list[dict[str, Any]] = []
    for row in by_model_raw:
        if not isinstance(row, dict):
            continue
        model_id = str(
            row.get("model_id") or row.get("modelId") or row.get("model") or "unknown"
        )
        requests = int(row.get("requests") or 0)
        input_tokens = int(
            row.get("input_tokens") or row.get("inputTokens") or row.get("prompt_tokens") or 0
        )
        output_tokens = int(
            row.get("output_tokens")
            or row.get("outputTokens")
            or row.get("completion_tokens")
            or 0
        )
        cost_usd = float(row.get("cost_usd") or row.get("costUsd") or 0.0)
        by_model.append(
            {
                "model_id": model_id,
                "modelId": model_id,
                "requests": requests,
                "input_tokens": input_tokens,
                "inputTokens": input_tokens,
                "output_tokens": output_tokens,
                "outputTokens": output_tokens,
                "cost_usd": cost_usd,
                "costUsd": cost_usd,
            }
        )
    by_model.sort(key=lambda r: (r["cost_usd"], r["requests"]), reverse=True)

    total_requests = payload.get("total_requests")
    if total_requests is None:
        total_requests = payload.get("totalRequests")
    if total_requests is None:
        total_requests = sum(r["requests"] for r in by_model)

    total_cost = payload.get("total_cost_usd")
    if total_cost is None:
        total_cost = payload.get("totalCostUsd")
    if total_cost is None:
        total_cost = sum(r["cost_usd"] for r in by_model)

    authenticated = payload.get("authenticated")
    if authenticated is None:
        authenticated = payload.get("source") == "gateway"

    return {
        "range": str(payload.get("range") or "billing_period"),
        "source": str(payload.get("source") or "gateway"),
        "note": payload.get("note"),
        "error": payload.get("error"),
        "authenticated": bool(authenticated),
        "byModel": by_model,
        "by_model": by_model,
        "total_requests": int(total_requests or 0),
        "totalRequests": int(total_requests or 0),
        "total_cost_usd": float(total_cost or 0.0),
        "totalCostUsd": float(total_cost or 0.0),
    }


def build_usage_activity(
    summary: dict[str, Any] | None = None,
    *,
    days: int = 371,
) -> dict[str, Any]:
    """
    Build a year-ish daily heatmap series for Profile.

    Gateway currently exposes aggregates, not a daily series — so we emit an
    empty grid and, when summary has by-model totals, attach them to *today*
    so hover can show model names + tokens honestly.
    """
    from datetime import date, timedelta

    today = date.today()
    start = today - timedelta(days=max(1, days) - 1)
    by_model = []
    if isinstance(summary, dict):
        raw = summary.get("byModel") or summary.get("by_model") or []
        if isinstance(raw, list):
            by_model = [r for r in raw if isinstance(r, dict)]

    day_list: list[dict[str, Any]] = []
    cursor = start
    while cursor <= today:
        models: list[dict[str, Any]] = []
        total_tokens = 0
        if cursor == today and by_model:
            for row in by_model:
                mid = str(row.get("modelId") or row.get("model_id") or "unknown")
                inn = int(row.get("inputTokens") or row.get("input_tokens") or 0)
                out = int(row.get("outputTokens") or row.get("output_tokens") or 0)
                req = int(row.get("requests") or 0)
                cost = float(row.get("costUsd") or row.get("cost_usd") or 0.0)
                models.append(
                    {
                        "modelId": mid,
                        "model_id": mid,
                        "inputTokens": inn,
                        "input_tokens": inn,
                        "outputTokens": out,
                        "output_tokens": out,
                        "requests": req,
                        "costUsd": cost,
                        "cost_usd": cost,
                    }
                )
                total_tokens += inn + out
        day_list.append(
            {
                "date": cursor.isoformat(),
                "totalTokens": total_tokens,
                "total_tokens": total_tokens,
                "models": models,
            }
        )
        cursor += timedelta(days=1)

    authenticated = True
    note = None
    if isinstance(summary, dict):
        authenticated = bool(summary.get("authenticated", True))
        note = summary.get("note")
        if not by_model and not authenticated:
            note = note or "Sign in to see live gateway usage."

    total_tokens = sum(int(d.get("totalTokens") or 0) for d in day_list)
    return {
        "range": "year",
        "granularity": "day",
        "authenticated": authenticated,
        "note": note,
        "days": day_list,
        "totalTokens": total_tokens,
        "total_tokens": total_tokens,
        "peakTokens": total_tokens,
        "peak_tokens": total_tokens,
    }
