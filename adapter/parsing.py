"""Parse a model reply into a validated Action, failing honestly.

Three outcomes, kept distinct because they mean different things about a model:
- a usable Action (the model played);
- a REFUSED action (the model deliberately declined, in JSON or in plain prose);
- a malformed result that the caller reprompts once, then abstains as MALFORMED.

This layer does NOT police legality (e.g. whether a vote target is alive). The
engine validates every action and downgrades illegal ones to ILLEGAL. Here we
only map output to an Action and flag refusals and parse failures.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

from engine.types import Action, ActionOutcome, ActionType, Role, Stance
from engine.views import PlayerView
from engine.types import Phase

# Meta-refusal phrasing, checked ONLY when no JSON object is present, so a normal
# in-character line like "I can't believe Player 3" is never misread as a refusal.
_REFUSAL_PHRASES = (
    "as an ai",
    "i cannot assist",
    "i can't assist",
    "i won't participate",
    "i will not participate",
    "i'm not able to help",
    "i am not able to help",
    "i cannot help with",
    "i must decline",
    "i decline to participate",
    "i'm not comfortable",
    "i am not comfortable",
)


@dataclass
class ParseResult:
    """Either an action to use, or an error to reprompt on. Never both."""

    action: Optional[Action] = None
    error: Optional[str] = None


def _strip_fences(text: str) -> str:
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    return fenced.group(1) if fenced else text


def _extract_json(text: str) -> Optional[dict]:
    """Find and parse the first JSON object in the reply.

    Tolerates surrounding prose and code fences. Scans for a balanced object,
    respecting string literals so braces inside a message do not confuse it.
    """
    candidate = _strip_fences(text).strip()
    try:
        obj = json.loads(candidate)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, ValueError):
        pass

    start = candidate.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(candidate)):
            ch = candidate[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(candidate[start : i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except (json.JSONDecodeError, ValueError):
                        break
        start = candidate.find("{", start + 1)
    return None


def _looks_like_refusal(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in _REFUSAL_PHRASES)


def _coerce_seat(value: object) -> Optional[int]:
    """Pull a seat number out of an int, a digit string, or 'Player N'."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        match = re.search(r"\d+", value)
        if match:
            return int(match.group())
    return None


def _parse_stance(value: object) -> Optional[Stance]:
    if isinstance(value, str):
        try:
            return Stance(value.strip().lower())
        except ValueError:
            return None
    return None


def _parse_lean(value: object) -> tuple[Optional[int], Optional[float]]:
    if not isinstance(value, dict):
        return None, None
    target = _coerce_seat(value.get("target"))
    confidence = value.get("confidence")
    try:
        conf = float(confidence) if confidence is not None else None
    except (TypeError, ValueError):
        conf = None
    if conf is not None:
        conf = max(0.0, min(1.0, conf))
    return target, conf


def _refusal_action(seat_id: int, reasoning: Optional[str]) -> Action:
    return Action(
        seat_id=seat_id,
        action_type=ActionType.ABSTAIN,
        outcome=ActionOutcome.REFUSED,
        note="model declined to act",
        private_reasoning=reasoning,
    )


def parse_reply(text: str, view: PlayerView) -> ParseResult:
    """Map one model reply to a ParseResult for the current phase."""
    seat = view.your_seat_id
    data = _extract_json(text)

    if data is None:
        if _looks_like_refusal(text):
            return ParseResult(action=_refusal_action(seat, text.strip()[:500]))
        return ParseResult(error="no JSON object found in reply")

    reasoning = data.get("private_reasoning")
    if not isinstance(reasoning, str):
        reasoning = None

    action_value = str(data.get("action", "")).strip().lower()

    if action_value in ("refuse", "abstain"):
        return ParseResult(action=_refusal_action(seat, reasoning))

    if view.phase is Phase.NIGHT:
        # The expected night action depends on this seat's role.
        expected = {
            Role.WEREWOLF: ("kill", ActionType.KILL),
            Role.SEER: ("investigate", ActionType.INVESTIGATE),
            Role.HEALER: ("protect", ActionType.PROTECT),
        }.get(view.your_role)
        if expected is None:
            return ParseResult(error=f"role {view.your_role.value} has no night action")
        keyword, action_type = expected
        if action_value != keyword:
            return ParseResult(
                error=f"expected action '{keyword}' at night, got '{action_value}'"
            )
        target = _coerce_seat(data.get("target"))
        if target is None:
            return ParseResult(error=f"{keyword} requires a numeric 'target'")
        return ParseResult(action=Action(
            seat_id=seat,
            action_type=action_type,
            target_seat_id=target,
            private_reasoning=reasoning,
        ))

    if view.phase is Phase.DAY_DISCUSSION:
        if action_value != "speak":
            return ParseResult(error=f"expected action 'speak' in discussion, got '{action_value}'")
        message = data.get("message")
        if not isinstance(message, str) or not message.strip():
            return ParseResult(error="speak requires a non-empty 'message'")
        lean_target, lean_conf = _parse_lean(data.get("lean"))
        return ParseResult(action=Action(
            seat_id=seat,
            action_type=ActionType.SPEAK,
            message=message,
            private_reasoning=reasoning,
            stance=_parse_stance(data.get("stance")),
            lean_target_seat_id=lean_target,
            lean_confidence=lean_conf,
        ))

    if view.phase is Phase.DAY_VOTE:
        if action_value != "vote":
            return ParseResult(error=f"expected action 'vote', got '{action_value}'")
        target = _coerce_seat(data.get("target"))
        if target is None:
            return ParseResult(error="vote requires a numeric 'target'")
        return ParseResult(action=Action(
            seat_id=seat,
            action_type=ActionType.VOTE,
            target_seat_id=target,
            private_reasoning=reasoning,
        ))

    return ParseResult(error=f"no action expected in phase {view.phase.value}")
