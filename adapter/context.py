"""Deterministic context construction from engine events.

Two distinct sources, kept strictly separate (see PLAN "Context construction"):

1. Structured game facts (roster, deaths, full voting history) rendered verbatim
   from PublicEvents. These are ground truth and are NEVER summarised — a model
   must not re-derive who voted for whom from prose.
2. Discussion narrative: the public SPEAK messages, sent in full in Season 1
   (no summariser).

Everything here is pure and sources only from PublicEvents and the seat roster,
which are public by construction. It never touches Action.private_reasoning or
any hidden field, so the information boundary holds by construction.
"""

from __future__ import annotations

from engine.types import PublicEvent
from engine.views import PlayerView


def _player(seat_id: int | None) -> str:
    return f"Player {seat_id}" if seat_id is not None else "Someone"


def render_roster(view: PlayerView) -> str:
    """Who is alive and who is dead, in seat order."""
    lines = []
    for seat in view.seats:
        status = "alive" if seat.alive else "dead"
        marker = " (you)" if seat.seat_id == view.your_seat_id else ""
        lines.append(f"- {seat.model_label}{marker}: {status}")
    return "\n".join(lines)


def _voting_history(events: list[PublicEvent]) -> list[str]:
    """One line per round: who voted for whom, then who was eliminated.

    The single most important fact to preserve exactly (PLAN). Rounds are shown
    1-indexed for readability; the engine counts from zero internally.
    """
    votes_by_round: dict[int, list[str]] = {}
    outcome_by_round: dict[int, str] = {}
    for e in events:
        if e.kind == "vote":
            votes_by_round.setdefault(e.round_number, []).append(
                f"{_player(e.seat_id)}->{_player(e.target_seat_id)}"
            )
        elif e.kind == "eliminated":
            role = f" ({e.revealed_role})" if e.revealed_role else ""
            outcome_by_round[e.round_number] = f"{_player(e.target_seat_id)} voted out{role}"
        elif e.kind == "no_elimination":
            outcome_by_round[e.round_number] = "no elimination"

    lines = []
    for rnd in sorted(set(votes_by_round) | set(outcome_by_round)):
        votes = ", ".join(votes_by_round.get(rnd, [])) or "no votes cast"
        outcome = outcome_by_round.get(rnd, "vote pending")
        lines.append(f"- Round {rnd + 1}: {votes} => {outcome}")
    return lines


def _night_deaths(events: list[PublicEvent]) -> list[str]:
    lines = []
    for e in events:
        if e.kind == "killed":
            role = f" ({e.revealed_role})" if e.revealed_role else ""
            lines.append(f"- Round {e.round_number + 1}: {_player(e.target_seat_id)} killed in the night{role}")
        elif e.kind == "no_kill":
            lines.append(f"- Round {e.round_number + 1}: no one killed in the night")
    return lines


def render_facts(view: PlayerView) -> str:
    """The always-correct facts block injected verbatim into every prompt."""
    sections = [f"Players:\n{render_roster(view)}"]

    deaths = _night_deaths(view.public_events)
    if deaths:
        sections.append("Night outcomes:\n" + "\n".join(deaths))

    votes = _voting_history(view.public_events)
    if votes:
        sections.append("Voting history:\n" + "\n".join(votes))

    return "\n\n".join(sections)


def render_discussion(view: PlayerView) -> str:
    """The full public discussion, verbatim. No summariser in Season 1."""
    lines = [e.detail for e in view.public_events if e.kind == "speak"]
    if not lines:
        return "(no discussion yet)"
    return "\n".join(lines)
