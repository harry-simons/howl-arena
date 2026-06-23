"""Core types for the werewolf game engine.

This module defines the data structures shared across the engine. It is
deliberately free of any game logic or model-aware code: it only describes
the shape of the world.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Role(Enum):
    """Secret role assigned to a player at game start.

    SEER and HEALER are special village roles (added in v2). They are
    mechanically villagers for the win condition and for scoring — they feed the
    villager ladder, not a new one (see PLAN "special roles — unranked
    variants"). Use is_village_team() rather than comparing to VILLAGER.
    """

    WEREWOLF = "werewolf"
    VILLAGER = "villager"
    SEER = "seer"       # village: each night, learns one player's alignment
    HEALER = "healer"   # village: each night, protects one player from the kill

    def is_village_team(self) -> bool:
        """Everyone who is not a werewolf is on the village side."""
        return self is not Role.WEREWOLF


class Phase(Enum):
    """Current phase of the game loop."""

    NIGHT = "night"
    DAY_DISCUSSION = "day_discussion"
    DAY_VOTE = "day_vote"
    ENDED = "ended"


class Team(Enum):
    """Winning side at game end."""

    WEREWOLVES = "werewolves"
    VILLAGERS = "villagers"


class ActionType(Enum):
    """The kinds of action a player can submit."""

    KILL = "kill"              # night: a werewolf nominates a target
    INVESTIGATE = "investigate"  # night: the seer checks one player's alignment
    PROTECT = "protect"        # night: the healer shields one player from the kill
    SPEAK = "speak"            # day: a player makes a public statement
    VOTE = "vote"              # day: a player votes to eliminate someone
    ABSTAIN = "abstain"        # any: explicit non-action (refusal, timeout, illegal)


class Stance(Enum):
    """Optional tag on a day-discussion SPEAK action. Drives readable replays
    and (later) a speaking-order rule that prioritises defence then attack."""

    ATTACK = "attack"      # press a case on someone
    DEFENSE = "defense"    # answer a case against oneself
    ANALYSIS = "analysis"  # neutral read or synthesis
    PASS = "pass"          # decline to add anything substantive


class ActionOutcome(Enum):
    """How an attempted action resolved. Separates intent from mechanics.

    Refusals and parse failures are distinguished because they mean different
    things about a model: REFUSED is a deliberate guardrail trip, MALFORMED is
    a capability failure.
    """

    ACCEPTED = "accepted"
    REFUSED = "refused"      # model explicitly declined to act in role
    MALFORMED = "malformed"  # output could not be parsed into a valid action
    TIMEOUT = "timeout"      # model did not respond in time
    ILLEGAL = "illegal"      # action parsed but broke a rule (e.g. voting dead)


@dataclass
class Player:
    """A seat in the game. The model identity lives here, the role is secret."""

    seat_id: int
    model_id: str
    role: Role
    alive: bool = True


@dataclass
class Action:
    """A single submitted action and how it resolved."""

    seat_id: int
    action_type: ActionType
    target_seat_id: Optional[int] = None  # for KILL / VOTE
    message: Optional[str] = None         # for SPEAK (the public utterance)
    outcome: ActionOutcome = ActionOutcome.ACCEPTED
    note: Optional[str] = None            # human-readable reason for non-ACCEPTED
    # Spectator-facing extras. NEVER shown to other players, only in replays.
    private_reasoning: Optional[str] = None   # the model's hidden thought this turn
    stance: Optional["Stance"] = None         # for SPEAK: attack/defense/analysis/pass
    # Stated current vote lean at the time of a day message, for swing tracking.
    lean_target_seat_id: Optional[int] = None
    lean_confidence: Optional[float] = None   # 0.0 to 1.0
    # Adapter-populated audit metadata: the OpenRouter upstream provider and the
    # enforced quantization that produced this action's (final) reply. Lets
    # scoring/analysis attribute malformed/illegal/timeout outcomes to a provider
    # and a precision level.
    provider: Optional[str] = None
    quant: Optional[str] = None
    # When this action happened, stamped by the engine on submission. Makes the
    # stored record self-describing so a replay can place each turn (and pair a
    # public message with its private reasoning) without re-deriving order.
    round_number: Optional[int] = None
    phase: Optional["Phase"] = None


@dataclass
class PublicEvent:
    """An event visible to all players and to spectators.

    The transcript is the spectator product, so events are stored in a form
    that reads cleanly in order.
    """

    round_number: int
    phase: Phase
    seat_id: Optional[int]      # actor, or None for engine-level events
    kind: str                   # e.g. "speak", "vote", "eliminated", "killed"
    detail: str                 # rendered text for the transcript
    target_seat_id: Optional[int] = None
    # Role revealed on a death/elimination (v2 role reveal). The value of a Role,
    # e.g. "werewolf"/"seer"; None for non-death events.
    revealed_role: Optional[str] = None


@dataclass
class GameResult:
    """Final outcome of a completed game."""

    winner: Team
    rounds_played: int
    surviving_seat_ids: list[int]
    final_roles: dict[int, Role]  # seat_id -> role, revealed at end


@dataclass
class GameCost:
    """Per-game cost, totalled across all model calls. Populated by the adapter
    layer from provider response metadata (e.g. OpenRouter usage/price)."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_cost: float = 0.0   # in the currency below
    currency: str = "USD"
    calls: int = 0            # number of model calls made


@dataclass
class GameRecord:
    """Everything produced by one game. Consumed by scoring and the site.

    Kept fully serialisable so games can be stored, replayed, and re-scored
    without re-running the models.
    """

    game_id: str
    seed: int
    seat_models: dict[int, str]            # seat_id -> model_id
    seat_roles: dict[int, str]             # seat_id -> role value
    events: list[PublicEvent] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)  # full audit, incl. night
    result: Optional[GameResult] = None
    cost: Optional["GameCost"] = None      # whole-game total, populated by adapter
    # Per-model cost breakdown (model_id -> GameCost). Combined with seat_roles
    # this supports the leaderboard's cost-per-model / value-for-money stat and
    # even cost-by-role, and survives persistence so it can be re-aggregated.
    model_costs: dict[str, "GameCost"] = field(default_factory=dict)
    # Experimental-rigour metadata (see Validity threats in PLAN.md).
    prompt_version: Optional[str] = None   # fixed per season; changing it = new season
    season_id: Optional[str] = None        # ratings only compare within a season
    summariser_version: Optional[str] = None  # None if no summariser used (v1 default)
    void: bool = False                     # excluded from scoring if a degenerate game
    void_reason: Optional[str] = None
