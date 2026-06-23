"""Builds the redacted view of game state visible to a single player.

This is a security boundary: a villager must never receive information only a
werewolf legitimately knows. The engine produces these views; the player
adapter (model-aware layer, built later) only ever sees one of these, never the
full GameState.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .types import Phase, PublicEvent, Role


@dataclass
class SeatView:
    """Public, always-visible facts about one seat."""

    seat_id: int
    model_label: str  # anonymised label shown to players, e.g. "Player 3"
    alive: bool


@dataclass
class PlayerView:
    """What a single player is allowed to know at decision time.

    Constructed fresh for every action request so it always reflects current
    state and never leaks future or hidden information.
    """

    your_seat_id: int
    your_role: Role
    round_number: int
    phase: Phase
    seats: list[SeatView]
    # Public transcript of everything said and voted so far, as rendered lines.
    public_log: list[str]
    # Only populated for werewolves: the seat ids of fellow wolves (incl. self).
    known_werewolf_seat_ids: list[int]
    # Only populated for werewolves at night: nominations submitted so far by
    # fellow wolves this night, as wolf_seat -> target_seat. Lets a wolf see
    # and align with team-mates' choices. Never shown to villagers.
    fellow_wolf_nominations: dict[int, int]
    # Valid targets for the action being requested (alive, rule-legal seats).
    valid_target_seat_ids: list[int]
    # Only populated for the seer: results of its past investigations, as
    # seat_id -> "werewolf" | "not werewolf". This is powerful hidden knowledge
    # and must never appear in any other seat's view.
    known_alignments: dict[int, str] = field(default_factory=dict)
    # Public game composition (role -> count), e.g. the setup everyone knows.
    # Carries no hidden information — just how many of each role are in play.
    role_setup: dict[str, int] = field(default_factory=dict)
    # The same public history as structured records (kind/seat/target/round), so
    # the adapter can cleanly separate deterministic game facts (votes, deaths)
    # from discussion narrative without string-parsing the rendered log. Every
    # entry is already public; this exposes no hidden information.
    public_events: list[PublicEvent] = field(default_factory=list)

    def is_werewolf(self) -> bool:
        return self.your_role is Role.WEREWOLF
