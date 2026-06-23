"""Exercises the engine end to end with scripted agents.

Run: python -m tests.smoke_test  (from the werewolf directory)

Checks:
1. A full game runs to a valid win condition.
2. Replays from the same seed are byte-identical (determinism).
3. Information boundaries hold (villagers never see wolf identities).
4. Refusal and malformed paths are recorded with the right outcomes.
"""

from __future__ import annotations

import random

from engine.game import GameConfig, GameState
from engine.runner import MatchRunner, ScriptedAgent
from engine.types import ActionOutcome, Phase, Role, Team


def build_agents(seed: int, refuse_seat: int | None = None, malformed_seat: int | None = None):
    agents = []
    for i in range(7):
        agents.append(
            ScriptedAgent(
                model_id=f"model_{i}",
                rng=random.Random(seed * 100 + i),
                refuse_wolf_kill=(i == refuse_seat),
                emit_malformed=(i == malformed_seat),
            )
        )
    return agents


def transcript(state: GameState) -> list[str]:
    return [e.detail for e in state.events]


def test_full_game_runs():
    config = GameConfig(num_players=7, num_werewolves=2)
    runner = MatchRunner(config)
    state = runner.run("g1", seed=42, agents=build_agents(42))

    assert state.phase is Phase.ENDED, "game did not end"
    assert state.result is not None
    assert state.result.winner in (Team.WEREWOLVES, Team.VILLAGERS)
    # Sanity: end condition is internally consistent.
    wolves = sum(1 for r in state.result.final_roles.values() if r is Role.WEREWOLF)
    assert wolves == 2
    print(f"[1] full game ran -> {state.result.winner.value} in "
          f"{state.result.rounds_played} round(s)")


def test_determinism():
    config = GameConfig(num_players=7, num_werewolves=2)
    runner = MatchRunner(config)
    a = runner.run("g", seed=99, agents=build_agents(99))
    b = runner.run("g", seed=99, agents=build_agents(99))
    assert transcript(a) == transcript(b), "replays diverged"
    assert a.result.winner is b.result.winner
    print(f"[2] determinism holds across replays ({len(transcript(a))} events)")


def test_information_boundary():
    config = GameConfig(num_players=7, num_werewolves=2)
    state = GameState("g", seed=7, config=config)
    state.assign_roles([f"model_{i}" for i in range(7)])

    wolf_seats = set(state.alive_wolves())
    for seat_id in range(7):
        view = state.build_player_view(seat_id)
        if state.players[seat_id].role is Role.VILLAGER:
            assert view.known_werewolf_seat_ids == [], (
                f"villager seat {seat_id} can see wolves: leak"
            )
        else:
            assert set(view.known_werewolf_seat_ids) == wolf_seats, (
                "wolf cannot see its own team"
            )
    print(f"[3] information boundary holds (wolves at seats {sorted(wolf_seats)})")


def test_refusal_and_malformed_recorded():
    config = GameConfig(num_players=7, num_werewolves=2)
    state = GameState("g", seed=7, config=config)
    state.assign_roles([f"model_{i}" for i in range(7)])
    wolf_seats = sorted(state.alive_wolves())

    # Force one wolf to refuse and run a single night manually.
    refusing_wolf = wolf_seats[0]
    agents = []
    for i in range(7):
        agents.append(
            ScriptedAgent(
                model_id=f"model_{i}",
                rng=random.Random(i),
                refuse_wolf_kill=(i == refusing_wolf),
            )
        )

    runner = MatchRunner(config)
    full = runner.run("g", seed=7, agents=agents)

    refused = [a for a in full.actions if a.outcome is ActionOutcome.REFUSED]
    assert refused, "refusal was not recorded in the audit trail"
    assert all(a.seat_id == refusing_wolf for a in refused)
    print(f"[4a] refusal recorded for seat {refusing_wolf} "
          f"({len(refused)} refused action(s))")

    # Malformed path on a villager.
    mal_agents = build_agents(7, malformed_seat=3)
    full2 = runner.run("g", seed=7, agents=mal_agents)
    malformed = [a for a in full2.actions if a.outcome is ActionOutcome.MALFORMED]
    assert malformed, "malformed output was not recorded"
    print(f"[4b] malformed output recorded ({len(malformed)} action(s))")


def test_wolf_coordination():
    """Both wolves should converge on a single target via plurality, so the
    night kill is the one both nominated, not a silent overwrite."""
    config = GameConfig(num_players=7, num_werewolves=2)
    state = GameState("g", seed=11, config=config)
    state.assign_roles([f"model_{i}" for i in range(7)])
    wolf_seats = sorted(state.alive_wolves())

    agents = build_agents(11)
    runner = MatchRunner(config)

    # Run a single night manually to inspect the nominations.
    for seat_id in wolf_seats:
        view = state.build_player_view(seat_id)
        action = agents[seat_id].get_action(view)
        state.submit_action(action)

    # Second wolf should have seen and backed the first wolf's nomination.
    nominations = list(state._pending_night_kills.values())
    assert len(set(nominations)) == 1, (
        f"wolves split the vote: {state._pending_night_kills}"
    )
    state.resolve_night()
    killed_events = [e for e in state.events if e.kind == "killed"]
    assert len(killed_events) == 1
    print(f"[5] wolves coordinated on Player {nominations[0]} "
          f"(both of seats {wolf_seats} agreed)")


def test_v2_roles_game():
    """v2 format: 9 players, 3 wolves, 1 seer, 1 healer, 3 discussion rounds.
    Runs to a valid end, the seer's knowledge stays private, and replays are
    deterministic."""
    config = GameConfig(num_players=9, num_werewolves=3, num_seers=1, num_healers=1)
    agents9 = [ScriptedAgent(model_id=f"model_{i}", rng=random.Random(7 * 100 + i))
               for i in range(9)]
    runner = MatchRunner(config, discussion_rounds=3)
    a = runner.run("v2", seed=7, agents=agents9)
    assert a.phase is Phase.ENDED and a.result is not None

    # Seer boundary: only seer seats may carry known_alignments.
    for seat_id, player in a.players.items():
        view = a.build_player_view(seat_id)
        if player.role is not Role.SEER:
            assert view.known_alignments == {}, f"seat {seat_id} leaked seer knowledge"

    # Determinism across a full replay.
    agents_b = [ScriptedAgent(model_id=f"model_{i}", rng=random.Random(7 * 100 + i))
                for i in range(9)]
    b = MatchRunner(config, discussion_rounds=3).run("v2", seed=7, agents=agents_b)
    assert transcript(a) == transcript(b), "v2 replay diverged"
    print(f"[6] v2 roles game ran -> {a.result.winner.value} in "
          f"{a.result.rounds_played} round(s); seer boundary + determinism hold")


def print_sample_transcript():
    config = GameConfig(num_players=7, num_werewolves=2)
    runner = MatchRunner(config)
    state = runner.run("sample", seed=3, agents=build_agents(3))
    print("\n--- sample transcript (seed 3) ---")
    for line in transcript(state):
        print("  " + line)
    print("--- end ---\n")


if __name__ == "__main__":
    test_full_game_runs()
    test_determinism()
    test_information_boundary()
    test_refusal_and_malformed_recorded()
    test_wolf_coordination()
    test_v2_roles_game()
    print("\nAll checks passed.")
    print_sample_transcript()
