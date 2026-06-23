"""Run several live games and aggregate the results (Step 3 testing).

Runs N games over consecutive seeds with the same roster, then reports win
balance, total/average cost, and a per-model breakdown (times seated, role
split, and accepted/refused/malformed/timeout counts). This is for shaking out
the adapter across many games, NOT for rating — balanced seating and Glicko are
Step 5. Seat order is fixed to the roster here; roles are dealt by each seed.

Usage (from the werewolf_pkg directory):

    export OPENROUTER_API_KEY=...        # required, never committed
    python run_games.py [num_games] [base_seed] [concurrency]

Games run in a capped thread pool (concurrency), so wall-clock is roughly the
length of the slowest single game rather than the sum of all of them.
"""

from __future__ import annotations

import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from adapter.agent import OpenRouterAgent
from adapter.config import AdapterConfig, PROVIDER_PREFS
from adapter.openrouter import CostAccumulator, OpenRouterClient
from adapter.prompt import PROMPT_VERSION
from engine.game import GameConfig
from engine.runner import MatchRunner
from engine.types import ActionOutcome, Role, Team
from storage import DEFAULT_DIR, save_game
from run_match import DISCUSSION_ROUNDS, NUM_HEALERS, NUM_SEERS, NUM_WEREWOLVES, ROSTER, SEASON_ID

# Default number of games to run at once. Games are independent, so this is pure
# wall-clock speedup; the cap keeps concurrent calls to any one model bounded so
# rate limits stay manageable (the transport also retries 429s with backoff).
DEFAULT_CONCURRENCY = 5


def play_one(seed, config, transport, runner):
    """Play one whole game and return (seed, record, cost). Games share no
    mutable state, so this is safe to run in its own thread; each gets a fresh
    GameState, agents, and cost accumulator."""
    cost = CostAccumulator()
    agents = [
        OpenRouterAgent(model_id=m, transport=transport, config=config, cost=cost)
        for m in ROSTER
    ]
    state = runner.run(game_id=f"games-{seed}", seed=seed, agents=agents)
    record = state.to_record()
    record.cost = cost.to_game_cost()
    record.model_costs = cost.model_game_costs()
    record.prompt_version = PROMPT_VERSION
    record.season_id = SEASON_ID
    return seed, record, cost


def main(num_games: int, base_seed: int, concurrency: int) -> None:
    config = AdapterConfig.from_env()
    transport = OpenRouterClient(
        api_key=config.api_key,
        base_url=config.base_url,
        timeout=config.timeout,
        provider_prefs=PROVIDER_PREFS,
    )
    game_config = GameConfig(
        num_players=len(ROSTER),
        num_werewolves=NUM_WEREWOLVES,
        num_seers=NUM_SEERS,
        num_healers=NUM_HEALERS,
    )
    runner = MatchRunner(game_config, discussion_rounds=DISCUSSION_ROUNDS)

    wins = {Team.WEREWOLVES: 0, Team.VILLAGERS: 0}
    total_cost = 0.0
    any_price = False
    # Per-model tallies across all games. Seer/healer count as village side.
    seated = defaultdict(int)
    wolf_seatings = defaultdict(int)
    wolf_wins = defaultdict(int)
    village_seatings = defaultdict(int)
    village_wins = defaultdict(int)
    outcomes = defaultdict(lambda: defaultdict(int))   # model -> outcome -> count
    model_latency = defaultdict(float)  # model -> total seconds across calls
    model_lat_calls = defaultdict(int)  # model -> number of timed calls
    model_cost = defaultdict(float)     # model -> total $ across calls
    model_providers = defaultdict(Counter)  # model -> provider -> calls (to verify pins)
    prov_latency = defaultdict(float)   # provider -> total seconds
    prov_calls = defaultdict(int)       # provider -> calls
    prov_outcomes = defaultdict(lambda: defaultdict(int))  # provider -> outcome -> count

    seeds = [base_seed + i for i in range(num_games)]
    print(f"Running {num_games} games (seeds {base_seed}..{base_seed + num_games - 1}), "
          f"{len(ROSTER)} models, prompt {PROMPT_VERSION}, concurrency {concurrency}\n")

    # Games are independent and I/O-bound, so run them in a capped thread pool.
    # Each worker only plays its game and returns results; ALL aggregation runs
    # here on the main thread as games complete, so the shared tallies need no
    # locks. Games finish out of order; each line is labelled with its seed.
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(play_one, s, config, transport, runner): s for s in seeds}
        for future in as_completed(futures):
            seed = futures[future]
            try:
                seed, record, cost = future.result()
            except Exception as exc:  # a whole game failed unexpectedly
                print(f"  seed {seed}: FAILED ({type(exc).__name__}: {exc})")
                continue

            # Persist on the main thread (each game is its own file, no clash).
            save_game(record)

            winner = record.result.winner
            wins[winner] += 1
            total_cost += record.cost.total_cost
            any_price = any_price or cost.price_observed

            for seat_id, model in record.seat_models.items():
                role = Role(record.seat_roles[seat_id])
                seated[model] += 1
                if role is Role.WEREWOLF:
                    wolf_seatings[model] += 1
                    if winner is Team.WEREWOLVES:
                        wolf_wins[model] += 1
                else:  # villager, seer, or healer — all village side
                    village_seatings[model] += 1
                    if winner is Team.VILLAGERS:
                        village_wins[model] += 1
            for action in record.actions:
                model = record.seat_models[action.seat_id]
                outcomes[model][action.outcome] += 1
                if action.provider:
                    model_providers[model][action.provider] += 1
            for model, stats in cost.per_model.items():
                model_latency[model] += stats.latency_s
                model_lat_calls[model] += stats.calls
                model_cost[model] += stats.cost
            for provider, stats in cost.per_provider.items():
                prov_latency[provider] += stats.latency_s
                prov_calls[provider] += stats.calls
            for action in record.actions:
                prov_outcomes[action.provider or "(unknown)"][action.outcome] += 1

            fails = sum(
                1 for a in record.actions
                if a.outcome in (ActionOutcome.TIMEOUT, ActionOutcome.MALFORMED)
            )
            print(f"  seed {seed}: {winner.value:<11} in {record.result.rounds_played} round(s), "
                  f"{record.cost.calls} calls, ${record.cost.total_cost:.6f}, "
                  f"{fails} infra/parse failure(s)")

    print("\n=== aggregate ===")
    print(f"  games:    {num_games}")
    print(f"  wolves:   {wins[Team.WEREWOLVES]}   villagers: {wins[Team.VILLAGERS]}")
    if any_price:
        print(f"  cost:     ${total_cost:.6f} total, ${total_cost / num_games:.6f}/game")
    else:
        print("  cost:     unknown (no provider returned a price)")

    print("\n=== per-model (wins/seatings by side) ===")
    for model in ROSTER:
        oc = outcomes[model]
        oc_str = ", ".join(
            f"{o.value}={oc[o]}" for o in ActionOutcome if oc.get(o)
        ) or "never acted"
        avg_lat = model_latency[model] / model_lat_calls[model] if model_lat_calls[model] else 0.0
        provs = model_providers[model]
        prov_str = ", ".join(f"{p}({n})" for p, n in provs.most_common()) or "-"
        print(f"  {model:<40} seated {seated[model]} "
              f"(wolf {wolf_wins[model]}/{wolf_seatings[model]}, "
              f"village {village_wins[model]}/{village_seatings[model]})")
        print(f"  {'':<40}   provider: {prov_str}")
        print(f"  {'':<40}   avg latency: {avg_lat:.1f}s/call   spend: ${model_cost[model]:.4f}   outcomes: {oc_str}")

    # Slowest models first — the practical signal for trimming the roster.
    print("\n=== latency ranking (slowest first) ===")
    ranked = sorted(
        ((model_latency[m] / model_lat_calls[m], m) for m in ROSTER if model_lat_calls[m]),
        reverse=True,
    )
    for avg, model in ranked:
        print(f"  {avg:5.1f}s/call   {model}")

    # Per-provider view: which OpenRouter upstreams served calls, how fast, and
    # whether they produced bad output (malformed/illegal/timeout) — the start of
    # spotting weak providers / aggressive quantization.
    print("\n=== by provider ===")
    providers = sorted(
        set(prov_calls) | set(prov_outcomes),
        key=lambda p: prov_latency[p] / prov_calls[p] if prov_calls[p] else 0.0,
        reverse=True,
    )
    for p in providers:
        avg = prov_latency[p] / prov_calls[p] if prov_calls[p] else 0.0
        oc = prov_outcomes[p]
        oc_str = ", ".join(f"{o.value}={oc[o]}" for o in ActionOutcome if oc.get(o)) or "-"
        print(f"  {p:<22} {prov_calls[p]:>3} calls, {avg:5.1f}s/call   {oc_str}")

    print(f"\nSaved games (JSON + .txt transcript) to {DEFAULT_DIR}/{SEASON_ID}/")


if __name__ == "__main__":
    games = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    conc = int(sys.argv[3]) if len(sys.argv) > 3 else min(games, DEFAULT_CONCURRENCY)
    main(games, seed, conc)
