"""UX visible-state modeling — treat UI as a complete user experience.

For interactive / multiplayer features, model the visible states for each user
and require answers for: what they see, what they can do, loading/disabled,
slow network, opponent leave, timeout, and whether the next action is obvious.

Also emits viewport / a11y / overflow verification checklists.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import List, Optional, Sequence


@dataclass
class UxState:
    """One visible state in the user experience state machine."""

    id: str
    label: str
    user_sees: str = ""
    user_can_do: str = ""
    loading: str = ""
    disabled: str = ""
    slow_network: str = ""
    other_player_leaves: str = ""
    after_timeout: str = ""
    next_action_obvious: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def complete(self) -> bool:
        return bool(
            self.user_sees
            and self.user_can_do
            and self.next_action_obvious
        )


@dataclass
class UxVerificationItem:
    id: str
    label: str
    status: str = "pending"  # pending | pass | fail | skipped
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class UxModel:
    states: List[UxState] = field(default_factory=list)
    verification: List[UxVerificationItem] = field(default_factory=list)
    applicable: bool = False

    def to_dict(self) -> dict:
        return {
            "states": [s.to_dict() for s in self.states],
            "verification": [v.to_dict() for v in self.verification],
            "applicable": self.applicable,
        }

    @property
    def states_modeled(self) -> bool:
        return bool(self.states) and all(
            s.user_sees and s.user_can_do for s in self.states
        )


_UI_RE = re.compile(
    r"(?i)\b(ui|ux|page|frontend|browser|react|next\.?js|screen|lobby|"
    r"multiplayer|button|form|modal|dashboard|web\s*app)\b"
)

_MULTIPLAYER_STATES: Sequence[tuple[str, str]] = (
    ("entry", "Entry"),
    ("joining", "Joining"),
    ("lobby", "Lobby"),
    ("sending_challenge", "Sending challenge"),
    ("incoming_challenge", "Incoming challenge"),
    ("challenge_declined", "Challenge declined / expired"),
    ("match_starting", "Match starting"),
    ("selecting_move", "Selecting move"),
    ("waiting_opponent", "Waiting for opponent"),
    ("round_result", "Round result"),
    ("next_round", "Next round"),
    ("final_result", "Final result"),
    ("return_lobby", "Return to lobby"),
    ("error_reconnect", "Error / reconnection"),
)

_GENERIC_WEB_STATES: Sequence[tuple[str, str]] = (
    ("entry", "Entry"),
    ("loading", "Loading"),
    ("ready", "Ready / idle"),
    ("submitting", "Submitting"),
    ("success", "Success"),
    ("empty", "Empty"),
    ("error", "Error"),
)

_VERIFY_ITEMS: Sequence[tuple[str, str]] = (
    ("phone_viewport", "Phone-sized viewport"),
    ("desktop_viewport", "Desktop viewport"),
    ("keyboard_nav", "Keyboard navigation"),
    ("color_contrast", "Color contrast"),
    ("screen_reader", "Screen-reader names"),
    ("loading_states", "Loading states"),
    ("empty_states", "Empty states"),
    ("error_states", "Error states"),
    ("text_overflow", "Long nicknames / text overflow"),
    ("rapid_clicks", "Rapid repeated clicks"),
    ("refresh_reconnect", "Refresh / reconnection behavior"),
    ("multi_user_sync", "Two users seeing synchronized states"),
)


def ux_model_needed(requirements: str) -> bool:
    return bool(_UI_RE.search(requirements or ""))


def draft_ux_model(requirements: str) -> UxModel:
    text = requirements or ""
    if not ux_model_needed(text):
        return UxModel(applicable=False)

    multi = bool(
        re.search(
            r"(?i)\b(multiplayer|two\s+players?|lobby|challenge|opponent)\b", text
        )
    )
    templates = _MULTIPLAYER_STATES if multi else _GENERIC_WEB_STATES
    states = [
        UxState(
            id=sid,
            label=label,
            # Seed prompts the agent must fill — incomplete until answered
            user_sees=f"(describe what the user sees in '{label}')",
            user_can_do=f"(describe available actions in '{label}')",
            loading="Show pending indicator; keep prior context visible",
            disabled="Disable primary CTA while in-flight; prevent double-submit",
            slow_network="Keep optimistic UI honest; show retry if request stalls",
            other_player_leaves=(
                "Return to lobby with clear notice"
                if multi
                else "N/A"
            ),
            after_timeout="Expire pending action; return to a recoverable state",
            next_action_obvious=False,
        )
        for sid, label in templates
    ]
    verification = [
        UxVerificationItem(id=vid, label=label)
        for vid, label in _VERIFY_ITEMS
        if multi or vid != "multi_user_sync"
    ]
    return UxModel(states=states, verification=verification, applicable=True)


def mark_ux_verification(
    model: UxModel,
    item_id: str,
    *,
    status: str,
    detail: str = "",
) -> Optional[UxVerificationItem]:
    for v in model.verification:
        if v.id == item_id:
            v.status = status
            v.detail = detail
            return v
    return None


def format_ux_model(model: UxModel) -> str:
    if not model.applicable:
        return "UX model: (not applicable)"
    lines = [
        "UX visible-state model (fill every state before calling the UI done):",
        "",
    ]
    for s in model.states:
        lines.append(f"  ### {s.label}")
        lines.append(f"    Sees: {s.user_sees}")
        lines.append(f"    Can do: {s.user_can_do}")
        lines.append(f"    Loading: {s.loading}")
        lines.append(f"    Disabled: {s.disabled}")
        lines.append(f"    Slow network: {s.slow_network}")
        if s.other_player_leaves != "N/A":
            lines.append(f"    Other leaves: {s.other_player_leaves}")
        lines.append(f"    After timeout: {s.after_timeout}")
        lines.append(
            f"    Next action obvious: {'yes' if s.next_action_obvious else 'NO — fix'}"
        )
        lines.append("")
    lines.append("UX verification checklist:")
    for v in model.verification:
        mark = {"pass": "[x]", "fail": "[!]", "skipped": "[-]"}.get(v.status, "[ ]")
        lines.append(f"  {mark} {v.label}")
        if v.detail:
            lines.append(f"      → {v.detail}")
    lines.append("")
    lines.append(
        "A 9/10 experience is understandable, responsive, accessible, and complete — "
        "not necessarily visually elaborate."
    )
    return "\n".join(lines)
