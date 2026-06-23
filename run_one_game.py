"""Run ONE real game with model-backed players and report cost.

Usage (from the werewolf_pkg directory):

    export OPENROUTER_API_KEY=...        # required, never committed
    export OPENROUTER_MODEL=...          # which cheap model to seat (optional)
    python run_one_game.py [seed]

This is the Step 2 acceptance check: get one real game completing end to end,
and measure cost-per-game from real response metadata rather than estimates.
Every seat is played by the SAME model in this first cut; mixing models is
Step 3. Seat-to-model mapping and the prompt version are stamped onto the record.
"""

from __future__ import annotations

import sys

from adapter.agent import OpenRouterAgent
from adapter.config import AdapterConfig
from adapter.openrouter import CostAccumulator, OpenRouterClient
from adapter.prompt import PROMPT_VERSION
from engine.game import GameConfig
from engine.runner import MatchRunner

SEASON_ID = "season-1"
NUM_PLAYERS = 7
NUM_WEREWOLVES = 2


def main(seed: int) -> None:
    config = AdapterConfig.from_env()
    transport = OpenRouterClient(
        api_key=config.api_key,
        base_url=config.base_url,
        timeout=config.timeout,
    )
    cost = CostAccumulator()

    agents = [
        OpenRouterAgent(
            model_id=config.model_id,
            transport=transport,
            config=config,
            cost=cost,
        )
        for _ in range(NUM_PLAYERS)
    ]

    game_config = GameConfig(num_players=NUM_PLAYERS, num_werewolves=NUM_WEREWOLVES)
    runner = MatchRunner(game_config)
    state = runner.run(game_id=f"real-{seed}", seed=seed, agents=agents)

    record = state.to_record()
    record.cost = cost.to_game_cost()
    record.prompt_version = PROMPT_VERSION
    record.season_id = SEASON_ID

    print("\n--- transcript ---")
    for event in state.events:
        print("  " + event.detail)

    print("\n--- result ---")
    print(f"  winner: {record.result.winner.value}")
    print(f"  rounds: {record.result.rounds_played}")
    print(f"  model:  {config.model_id}")
    print(f"  prompt: {PROMPT_VERSION}  season: {SEASON_ID}")

    c = record.cost
    print("\n--- cost ---")
    print(f"  calls:         {c.calls}")
    print(f"  input tokens:  {c.input_tokens}")
    print(f"  output tokens: {c.output_tokens}")
    if cost.price_observed:
        print(f"  total cost:    {c.total_cost:.6f} {c.currency}")
    else:
        print("  total cost:    unknown (provider returned no price this run)")


if __name__ == "__main__":
    seed_arg = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    main(seed_arg)
