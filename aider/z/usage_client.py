"""
Gateway usage aggregate client for Profile (Phase 9).

Proxies authenticated ``GET /v1/gateway/usage?range=billing_period|all``
and normalizes the response for the desktop Profile panel.
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


def _stub_summary(range_key: str) -> dict[str, Any]:
    raw = os.environ.get("Z_GATEWAY_USAGE_STUB", "").strip()
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                data.setdefault("range", range_key)
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
    }


def fetch_usage_summary(
    range_key: str = "billing_period",
    *,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """
    Fetch usage aggregate for Profile.

    Prefer live gateway when credentials exist; fall back to stub/demo data
    so the panel always renders something useful offline.
    """
    key = (range_key or "billing_period").strip().lower()
    if key not in ALLOWED_RANGES:
        # Accept legacy aliases from early Profile drafts
        if key in {"today", "7d", "30d", "month"}:
            key = "billing_period"
        else:
            key = "billing_period"

    if os.environ.get("Z_GATEWAY_USAGE_STUB") or os.environ.get("Z_GATEWAY_STUB"):
        return _stub_summary(key)

    creds = load_credentials()
    if creds is None or not getattr(creds, "access_token", None):
        out = _stub_summary(key)
        out["source"] = "demo"
        out["authenticated"] = False
        out["note"] = "Sign in to load live gateway usage."
        return out

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
        out = _stub_summary(key)
        out["source"] = "fallback"
        out["authenticated"] = True
        out["note"] = f"Could not reach gateway usage ({exc}). Showing demo totals."
        return out


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
