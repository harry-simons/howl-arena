"""Scripted dummy players and a match runner.

These dummies stand in for model adapters so the engine can be exercised
deterministically before any API is wired in. They make plausible-but-simple
choices and deliberately include refusal and malformed behaviour so the
runner's handling of those paths is tested too.

The real model adapter (built later) will implement the same get_action
signature, so the runner does not change when models replace dummies.
"""

from __future__ import annotations

import random
from typing import Protocol

from .game import GameConfig, GameState
from .types import Action, ActionOutcome, ActionType, Phase, Role
from .views import PlayerView


class PlayerAgent(Protocol):
    """The single interface the runner depends on. Models will implement this."""

    model_id: str

    def get_action(self, view: PlayerView) -> Action:
        ...


class ScriptedAgent:
    """A simple deterministic agent for engine testing.

    Behaviour is driven by the seeded rng passed in, so whole-game replays stay
    deterministic. Optional flags inject refusal and malformed paths.
    """

    def __init__(
        self,
        model_id: str,
        rng: random.Random,
        refuse_wolf_kill: bool = False,
        emit_malformed: bool = False,
    ):
        self.model_id = model_id
        self._rng = rng
        self._refuse_wolf_kill = refuse_wolf_kill
        self._emit_malformed = emit_malformed

    def get_action(self, view: PlayerView) -> Action:
        seat = view.your_seat_id

        if view.phase is Phase.NIGHT:
            # The seer and healer also act at night; pick a random legal target.
            if view.your_role is Role.SEER:
                target = self._rng.choice(view.valid_target_seat_ids)
                return Action(seat_id=seat, action_type=ActionType.INVESTIGATE, target_seat_id=target)
            if view.your_role is Role.HEALER:
                target = self._rng.choice(view.valid_target_seat_ids)
                return Action(seat_id=seat, action_type=ActionType.PROTECT, target_seat_id=target)
            # Otherwise this is a wolf being asked for the kill.
            if self._refuse_wolf_kill:
                return Action(
                    seat_id=seat,
                    action_type=ActionType.ABSTAIN,
                    outcome=ActionOutcome.REFUSED,
                    note="scripted refusal to kill",
                )
            # Coordinate: if a fellow wolf has already nominated a valid target,
            # back it rather than splitting the vote. Otherwise pick at random.
            for nominated in view.fellow_wolf_nominations.values():
                if nominated in view.valid_target_seat_ids:
                    return Action(
                        seat_id=seat,
                        action_type=ActionType.KILL,
                        target_seat_id=nominated,
                    )
            target = self._rng.choice(view.valid_target_seat_ids)
            return Action(seat_id=seat, action_type=ActionType.KILL, target_seat_id=target)

        if view.phase is Phase.DAY_DISCUSSION:
            if self._emit_malformed:
                # Simulate output that the parser cannot turn into a valid action.
                return Action(
                    seat_id=seat,
                    action_type=ActionType.ABSTAIN,
                    outcome=ActionOutcome.MALFORMED,
                    note="scripted malformed output",
                )
            claim = "I think we should watch the quiet players."
            return Action(seat_id=seat, action_type=ActionType.SPEAK, message=claim)

        if view.phase is Phase.DAY_VOTE:
            target = self._rng.choice(view.valid_target_seat_ids)
            return Action(seat_id=seat, action_type=ActionType.VOTE, target_seat_id=target)

        return Action(seat_id=seat, action_type=ActionType.ABSTAIN)


class MatchRunner:
    """Drives one game from setup to result using a set of agents.

    Knows nothing about how agents decide. Handles the phase loop, requests
    actions only from seats entitled to act, and applies a single reprompt-free
    abstention fallback if an agent raises (a real adapter handles timeouts and
    reprompts itself; the runner's job is to never let one agent crash a game).
    """

    def __init__(self, config: GameConfig, max_rounds: int = 20, discussion_rounds: int = 1):
        self.config = config
        self.max_rounds = max_rounds
        # Statements each living player makes per day before the vote. Raising
        # this gives villagers more back-and-forth to find wolves; it is a
        # deliberate balance/experiment lever (see PLAN / v2 format notes).
        self.discussion_rounds = discussion_rounds

    def run(self, game_id: str, seed: int, agents: list[PlayerAgent], wolf_seats=None) -> GameState:
        state = GameState(game_id=game_id, seed=seed, config=self.config)
        state.assign_roles([a.model_id for a in agents], wolf_seats=wolf_seats)

        while state.phase is not Phase.ENDED and state.round_number < self.max_rounds:
            if state.phase is Phase.NIGHT:
                self._run_night(state, agents)
            elif state.phase is Phase.DAY_DISCUSSION:
                self._run_discussion(state, agents)
                # Discussion always rolls straight into the vote.
                if state.phase is Phase.DAY_DISCUSSION:
                    state.phase = Phase.DAY_VOTE
            elif state.phase is Phase.DAY_VOTE:
                self._run_vote(state, agents)

        return state

    def _safe_get_action(self, agent: PlayerAgent, state: GameState, seat_id: int) -> Action:
        view = state.build_player_view(seat_id)
        try:
            return agent.get_action(view)
        except Exception as exc:  # never let one agent crash the game
            return Action(
                seat_id=seat_id,
                action_type=ActionType.ABSTAIN,
                outcome=ActionOutcome.TIMEOUT,
                note=f"agent error: {exc}",
            )

    def _run_night(self, state: GameState, agents: list[PlayerAgent]) -> None:
        # The healer and seer act alongside the wolves. They act blind to each
        # other; the engine resolves protection, kill, then investigation.
        night_actors = (
            sorted(state.alive_healers())
            + sorted(state.alive_seers())
            + sorted(state.alive_wolves())
        )
        for seat_id in night_actors:
            action = self._safe_get_action(agents[seat_id], state, seat_id)
            state.submit_action(action)
        state.resolve_night()

    def _run_discussion(self, state: GameState, agents: list[PlayerAgent]) -> None:
        # discussion_rounds passes, one message per alive player per pass, in
        # seat order. Later passes see the earlier statements in the public log.
        for _ in range(self.discussion_rounds):
            for seat_id in sorted(state.alive_seats()):
                action = self._safe_get_action(agents[seat_id], state, seat_id)
                state.submit_action(action)

    def _run_vote(self, state: GameState, agents: list[PlayerAgent]) -> None:
        for seat_id in sorted(state.alive_seats()):
            action = self._safe_get_action(agents[seat_id], state, seat_id)
            state.submit_action(action)
        state.resolve_votes()
