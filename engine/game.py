"""Deterministic werewolf game engine.

Pure rules. No model calls, no I/O. Given a seed and a set of player decisions,
a game always replays identically. The engine validates every action and
records a full audit trail.

Design notes:
- The engine never calls models. It exposes a "what does this seat need to
  decide now" query and an "apply this action" method. The match runner (built
  later) wires those to the model adapters.
- Information boundaries are enforced by build_player_view, not trusted to the
  caller.
"""

from __future__ import annotations

import random
from collections import Counter
from typing import Optional

from .types import (
    Action,
    ActionOutcome,
    ActionType,
    GameRecord,
    GameResult,
    Phase,
    Player,
    PublicEvent,
    Role,
    Team,
)
from .views import PlayerView, SeatView


class GameConfig:
    """Validated game configuration.

    num_seers / num_healers default to 0, so a config that names only players and
    werewolves reproduces the original v1 game exactly (no special roles).
    """

    def __init__(
        self,
        num_players: int,
        num_werewolves: int,
        num_seers: int = 0,
        num_healers: int = 0,
    ):
        if num_players < 3:
            raise ValueError("need at least 3 players")
        if num_werewolves < 1:
            raise ValueError("need at least 1 werewolf")
        if num_seers < 0 or num_healers < 0:
            raise ValueError("role counts cannot be negative")
        if num_werewolves + num_seers + num_healers > num_players:
            raise ValueError("more assigned roles than seats")
        if num_werewolves * 2 >= num_players:
            # wolves at or above parity at start means villagers cannot win
            raise ValueError("werewolves must start below parity")
        self.num_players = num_players
        self.num_werewolves = num_werewolves
        self.num_seers = num_seers
        self.num_healers = num_healers


class GameState:
    """Holds the full, unredacted state. Only the engine touches this directly."""

    def __init__(self, game_id: str, seed: int, config: GameConfig):
        self.game_id = game_id
        self.seed = seed
        self.config = config
        self._rng = random.Random(seed)
        self.round_number = 0
        self.phase = Phase.NIGHT
        self.players: dict[int, Player] = {}
        self.events: list[PublicEvent] = []
        self.actions: list[Action] = []
        self.result: Optional[GameResult] = None
        # Working store for the current night's wolf kill nomination.
        # Working store for the current night: wolf_seat -> nominated target.
        # Resolved by plurality of wolf nominations, ties broken by seeded rng.
        self._pending_night_kills: dict[int, int] = {}
        # Current night's healer protection (seat protected), if any.
        self._pending_protect: Optional[int] = None
        # Current night's seer investigations: seer_seat -> investigated target.
        self._pending_investigations: dict[int, int] = {}
        # Accumulated seer knowledge across nights: seer_seat -> {target: alignment}.
        # Persists for the game; injected only into that seer's own view.
        self._seer_knowledge: dict[int, dict[int, str]] = {}
        # Working store for the current day's votes: voter_seat -> target_seat.
        self._pending_votes: dict[int, int] = {}

    # ----- setup -----------------------------------------------------------

    def assign_roles(self, model_ids: list[str], wolf_seats=None) -> None:
        """Seat the given models and deal roles using the seeded rng.

        model_ids order is preserved as seat order; role dealing is randomised
        by the seed so seating a model in a seat does not fix its role.

        wolf_seats (optional): force these seats to be the werewolves (used by
        the season scheduler to control which models co-wolf). Seer/healer are
        still dealt randomly among the rest. None = fully random (default), which
        keeps existing behaviour and the smoke test unchanged.
        """
        if len(model_ids) != self.config.num_players:
            raise ValueError("model count does not match configured players")

        seats = list(range(self.config.num_players))
        # Deal wolves first (preserving the exact v1 draw when there are no
        # special roles), then seers, then healers, from the remaining seats.
        if wolf_seats is None:
            wolf_seats = set(self._rng.sample(seats, self.config.num_werewolves))
        else:
            wolf_seats = set(wolf_seats)
            if len(wolf_seats) != self.config.num_werewolves or not wolf_seats <= set(seats):
                raise ValueError("wolf_seats must name exactly num_werewolves valid seats")
        remaining = [s for s in seats if s not in wolf_seats]
        seer_seats = (
            set(self._rng.sample(remaining, self.config.num_seers))
            if self.config.num_seers else set()
        )
        remaining = [s for s in remaining if s not in seer_seats]
        healer_seats = (
            set(self._rng.sample(remaining, self.config.num_healers))
            if self.config.num_healers else set()
        )

        for seat_id, model_id in enumerate(model_ids):
            if seat_id in wolf_seats:
                role = Role.WEREWOLF
            elif seat_id in seer_seats:
                role = Role.SEER
            elif seat_id in healer_seats:
                role = Role.HEALER
            else:
                role = Role.VILLAGER
            self.players[seat_id] = Player(seat_id=seat_id, model_id=model_id, role=role)

    # ----- queries ---------------------------------------------------------

    def alive_seats(self) -> list[int]:
        return [s for s, p in self.players.items() if p.alive]

    def alive_wolves(self) -> list[int]:
        return [s for s in self.alive_seats() if self.players[s].role is Role.WEREWOLF]

    def alive_villagers(self) -> list[int]:
        return [s for s in self.alive_seats() if self.players[s].role is Role.VILLAGER]

    def alive_non_wolves(self) -> list[int]:
        """Everyone alive on the village side (plain villagers, seer, healer)."""
        return [s for s in self.alive_seats() if self.players[s].role.is_village_team()]

    def alive_seers(self) -> list[int]:
        return [s for s in self.alive_seats() if self.players[s].role is Role.SEER]

    def alive_healers(self) -> list[int]:
        return [s for s in self.alive_seats() if self.players[s].role is Role.HEALER]

    def _seat_views(self) -> list[SeatView]:
        return [
            SeatView(seat_id=s, model_label=f"Player {s}", alive=p.alive)
            for s, p in sorted(self.players.items())
        ]

    def _public_log(self) -> list[str]:
        return [e.detail for e in self.events]

    def build_player_view(self, seat_id: int) -> PlayerView:
        """Construct the redacted view this seat is allowed to see right now.

        valid_target_seat_ids reflects the action currently being requested,
        which depends on the phase and the seat's role.
        """
        player = self.players[seat_id]

        known_wolves: list[int] = []
        fellow_nominations: dict[int, int] = {}
        if player.role is Role.WEREWOLF:
            known_wolves = sorted(
                s for s, p in self.players.items() if p.role is Role.WEREWOLF
            )
            # Show this wolf the nominations already submitted by team-mates
            # (excluding its own) so it can coordinate the kill.
            fellow_nominations = {
                w: t for w, t in self._pending_night_kills.items() if w != seat_id
            }

        # Only the seer carries its own accumulated investigation results.
        known_alignments: dict[int, str] = {}
        if player.role is Role.SEER:
            known_alignments = dict(self._seer_knowledge.get(seat_id, {}))

        valid_targets = self._valid_targets_for(seat_id)

        return PlayerView(
            your_seat_id=seat_id,
            your_role=player.role,
            round_number=self.round_number,
            phase=self.phase,
            seats=self._seat_views(),
            public_log=self._public_log(),
            public_events=list(self.events),
            known_werewolf_seat_ids=known_wolves,
            fellow_wolf_nominations=fellow_nominations,
            valid_target_seat_ids=valid_targets,
            known_alignments=known_alignments,
            role_setup=self._role_setup(),
        )

    def _role_setup(self) -> dict[str, int]:
        """The public game composition (role -> count), known to everyone."""
        c = self.config
        villagers = c.num_players - c.num_werewolves - c.num_seers - c.num_healers
        return {
            Role.WEREWOLF.value: c.num_werewolves,
            Role.SEER.value: c.num_seers,
            Role.HEALER.value: c.num_healers,
            Role.VILLAGER.value: villagers,
        }

    def _valid_targets_for(self, seat_id: int) -> list[int]:
        """Legal targets for the seat given the current phase and role."""
        if self.phase is Phase.NIGHT:
            role = self.players[seat_id].role
            if role is Role.WEREWOLF:
                # wolves may kill any alive player on the village side
                return sorted(self.alive_non_wolves())
            if role is Role.SEER:
                # the seer may investigate any living player other than itself
                return sorted(s for s in self.alive_seats() if s != seat_id)
            if role is Role.HEALER:
                # the healer may protect any living player, including itself
                return sorted(self.alive_seats())
            return []
        if self.phase is Phase.DAY_VOTE:
            # may vote for any alive player other than self
            return sorted(s for s in self.alive_seats() if s != seat_id)
        return []

    # ----- action application ---------------------------------------------

    def _validate(self, action: Action) -> Action:
        """Check an action against the rules. Returns the action with outcome
        set; never raises on rule breaches (those are data, not exceptions)."""
        seat = action.seat_id
        if seat not in self.players or not self.players[seat].alive:
            action.outcome = ActionOutcome.ILLEGAL
            action.note = "actor not alive"
            return action

        # Pass through explicit non-actions untouched; the runner sets these.
        if action.outcome in (
            ActionOutcome.REFUSED,
            ActionOutcome.MALFORMED,
            ActionOutcome.TIMEOUT,
        ):
            return action

        if action.action_type in (
            ActionType.KILL,
            ActionType.VOTE,
            ActionType.INVESTIGATE,
            ActionType.PROTECT,
        ):
            valid = self._valid_targets_for(seat)
            if action.target_seat_id not in valid:
                action.outcome = ActionOutcome.ILLEGAL
                # Record WHY precisely, so the audit trail is an analyzable signal
                # rather than a discarded error. Targeting a dead player is the
                # most telling: it measures how well a model tracks game state.
                action.note = self._illegal_reason(seat, action.target_seat_id)
                return action

        action.outcome = ActionOutcome.ACCEPTED
        return action

    def _illegal_reason(self, seat: int, target: Optional[int]) -> str:
        """Classify why a target is illegal, as a stable, trackable statement."""
        if target is None:
            return "no target given"
        if target == seat:
            return "target_self"
        if target not in self.players:
            return f"target_nonexistent: Player {target}"
        if not self.players[target].alive:
            return f"target_dead: Player {target} is no longer alive"
        return f"target_illegal: Player {target} not allowed this phase"

    def submit_action(self, action: Action) -> Action:
        """Validate, record, and apply one action's effect on working state.

        Phase resolution (who actually dies) happens in the phase advance
        methods, not here, so that simultaneous actions (votes) accumulate
        before resolving.
        """
        action = self._validate(action)
        # Stamp the timeline position so the stored record is self-describing.
        action.round_number = self.round_number
        action.phase = self.phase
        self.actions.append(action)

        accepted = action.outcome is ActionOutcome.ACCEPTED

        if self.phase is Phase.NIGHT and action.action_type is ActionType.KILL:
            if accepted:
                # Record this wolf's nomination. Resolved by plurality in
                # resolve_night, so no wolf silently overwrites another.
                self._pending_night_kills[action.seat_id] = action.target_seat_id

        elif self.phase is Phase.NIGHT and action.action_type is ActionType.INVESTIGATE:
            if accepted:
                self._pending_investigations[action.seat_id] = action.target_seat_id

        elif self.phase is Phase.NIGHT and action.action_type is ActionType.PROTECT:
            if accepted:
                # One healer in v2; last accepted protection stands.
                self._pending_protect = action.target_seat_id

        elif self.phase is Phase.DAY_DISCUSSION and action.action_type is ActionType.SPEAK:
            if accepted and action.message:
                self.events.append(
                    PublicEvent(
                        round_number=self.round_number,
                        phase=self.phase,
                        seat_id=action.seat_id,
                        kind="speak",
                        detail=f"Player {action.seat_id}: {action.message.strip()}",
                    )
                )

        elif self.phase is Phase.DAY_VOTE and action.action_type is ActionType.VOTE:
            if accepted:
                self._pending_votes[action.seat_id] = action.target_seat_id
                self.events.append(
                    PublicEvent(
                        round_number=self.round_number,
                        phase=self.phase,
                        seat_id=action.seat_id,
                        kind="vote",
                        detail=f"Player {action.seat_id} votes for Player {action.target_seat_id}",
                        target_seat_id=action.target_seat_id,
                    )
                )

        return action

    # ----- phase resolution ------------------------------------------------

    @staticmethod
    def _role_label(role: Role) -> str:
        """How a revealed role reads in the transcript: 'the seer', 'a villager'."""
        if role in (Role.SEER, Role.HEALER):
            return f"the {role.value}"
        return f"a {role.value}"

    def resolve_night(self) -> None:
        """Resolve the night: healer protection, then the wolf kill, then the
        seer's investigation, then advance to day discussion.

        The kill is the plurality of wolf nominations (ties broken by seeded
        rng). If the killed seat was the healer's protected target, no one dies
        — and a save is reported identically to a genuine miss, so no player can
        tell a protection happened. The seer learns the alignment of its chosen
        target and keeps that knowledge for later rounds. The dead player's role
        is revealed on death (v2 role reveal).
        """
        protected = self._pending_protect

        killed: Optional[int] = None
        if self._pending_night_kills:
            tally = Counter(self._pending_night_kills.values())
            top = max(tally.values())
            tied = sorted(t for t, c in tally.items() if c == top)
            killed = tied[0] if len(tied) == 1 else self._rng.choice(tied)

        saved = killed is not None and killed == protected
        if killed is not None and not saved and self.players[killed].alive:
            self.players[killed].alive = False
            role = self.players[killed].role
            self.events.append(
                PublicEvent(
                    round_number=self.round_number,
                    phase=Phase.NIGHT,
                    seat_id=None,
                    kind="killed",
                    detail=f"Player {killed} was killed in the night. "
                           f"They were {self._role_label(role)}.",
                    target_seat_id=killed,
                    revealed_role=role.value,
                )
            )
        else:
            # Covers a genuine miss AND a successful protection — deliberately
            # indistinguishable to players so the healer's save stays secret.
            self.events.append(
                PublicEvent(
                    round_number=self.round_number,
                    phase=Phase.NIGHT,
                    seat_id=None,
                    kind="no_kill",
                    detail="No one was killed in the night.",
                )
            )

        # Resolve seer investigations into accumulated, private knowledge.
        for seer_seat, target in self._pending_investigations.items():
            alignment = (
                "werewolf" if self.players[target].role is Role.WEREWOLF
                else "not werewolf"
            )
            self._seer_knowledge.setdefault(seer_seat, {})[target] = alignment

        self._pending_night_kills = {}
        self._pending_protect = None
        self._pending_investigations = {}
        if not self._check_end():
            self.phase = Phase.DAY_DISCUSSION

    def resolve_votes(self) -> None:
        """Tally the day votes, eliminate the plurality target, advance phase.

        Ties are broken by the seeded rng among the tied seats, so replays stay
        deterministic. A round with zero votes eliminates no one.
        """
        if self._pending_votes:
            tally = Counter(self._pending_votes.values())
            top = max(tally.values())
            tied = sorted(s for s, c in tally.items() if c == top)
            eliminated = tied[0] if len(tied) == 1 else self._rng.choice(tied)
            self.players[eliminated].alive = False
            tie_note = " (tie broken at random)" if len(tied) > 1 else ""
            role = self.players[eliminated].role
            self.events.append(
                PublicEvent(
                    round_number=self.round_number,
                    phase=Phase.DAY_VOTE,
                    seat_id=None,
                    kind="eliminated",
                    detail=f"Player {eliminated} was voted out{tie_note}. "
                           f"They were {self._role_label(role)}.",
                    target_seat_id=eliminated,
                    revealed_role=role.value,
                )
            )
        else:
            self.events.append(
                PublicEvent(
                    round_number=self.round_number,
                    phase=Phase.DAY_VOTE,
                    seat_id=None,
                    kind="no_elimination",
                    detail="No votes were cast; no one was eliminated.",
                )
            )
        self._pending_votes = {}
        if not self._check_end():
            self.round_number += 1
            self.phase = Phase.NIGHT

    def _check_end(self) -> bool:
        """Set result and ENDED phase if a win condition is met."""
        wolves = len(self.alive_wolves())
        non_wolves = len(self.alive_non_wolves())

        winner: Optional[Team] = None
        if wolves == 0:
            winner = Team.VILLAGERS
        elif wolves >= non_wolves:
            winner = Team.WEREWOLVES

        if winner is not None:
            self.phase = Phase.ENDED
            self.result = GameResult(
                winner=winner,
                rounds_played=self.round_number + 1,
                surviving_seat_ids=sorted(self.alive_seats()),
                final_roles={s: p.role for s, p in self.players.items()},
            )
            self.events.append(
                PublicEvent(
                    round_number=self.round_number,
                    phase=Phase.ENDED,
                    seat_id=None,
                    kind="game_over",
                    detail=f"Game over. {winner.value} win.",
                )
            )
            return True
        return False

    # ----- export ----------------------------------------------------------

    def to_record(self) -> GameRecord:
        return GameRecord(
            game_id=self.game_id,
            seed=self.seed,
            seat_models={s: p.model_id for s, p in self.players.items()},
            seat_roles={s: p.role.value for s, p in self.players.items()},
            events=list(self.events),
            actions=list(self.actions),
            result=self.result,
        )
