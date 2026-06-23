"""Run ONE game with a roster of DISTINCT models (Step 3).

Confirms the adapter is provider-neutral: several different models play the same
game through the same prompt and parser, and nothing provider-specific leaks
into the engine or runner. Seat order follows the roster; roles are dealt
randomly by the seed, so which model plays wolf vs villager varies per seed.

Usage (from the werewolf_pkg directory):

    export OPENROUTER_API_KEY=...        # required, never committed
    python run_match.py [seed]

Each seat is one model; the cost accumulator is shared so the whole-game spend
totals onto the record. A per-model outcome summary makes refusals, malformed
replies, and provider errors visible at a glance.
"""

from __future__ import annotations

import sys
from collections import defaultdict

from adapter.agent import OpenRouterAgent
from adapter.config import AdapterConfig, PROVIDER_PREFS
from adapter.openrouter import CostAccumulator, OpenRouterClient
from adapter.prompt import PROMPT_VERSION
from engine.game import GameConfig
from engine.runner import MatchRunner
from engine.types import ActionOutcome
import scoring
from storage import save_game

SEASON_ID = "season-1"
# v2 format: 9 players, 2 wolves, 1 seer, 1 healer, 5 villagers; 3 statements
# per player per day. (Glicko scoring over many games is Step 5.)
NUM_WEREWOLVES = 2
NUM_SEERS = 1
NUM_HEALERS = 1
DISCUSSION_ROUNDS = 3

# Season 1 roster: nine models (all <=120B) across six labs, a deliberate spread
# of heavyweights / mids / underdogs. Providers pinned in PROVIDER_PREFS, leaning
# on fast silicon (Cerebras/Groq/SambaNova) where a model is served there.
ROSTER = [
    "openai/gpt-oss-120b",                # OpenAI, heavyweight  — Cerebras fp16
    "meta-llama/llama-3.3-70b-instruct",  # Meta, heavyweight    — SambaNova bf16
    "qwen/qwen3-next-80b-a3b-instruct",   # Qwen, heavyweight    — Alibaba (match-7 star)
    "qwen/qwen3-32b",                     # Qwen, mid (reasoning)— Groq
    "openai/gpt-oss-20b",                 # OpenAI, mid          — Groq
    "google/gemma-4-31b-it",              # Google, mid          — Novita bf16
    "z-ai/glm-4.5-air",                   # Z-AI, heavyweight    — Novita bf16 (106B/12B MoE)
    "mistralai/mistral-nemo",             # Mistral, underdog    — DeepInfra
    "amazon/nova-lite-v1",                # Amazon, underdog     — Amazon Bedrock
]
# (deepseek-v4-flash dropped: 284B total params, over the <=120B cap — only its
# 13B *active* params are small. "Flash" is speed, not size.)


def main(seed: int) -> None:
    config = AdapterConfig.from_env()
    transport = OpenRouterClient(
        api_key=config.api_key,
        base_url=config.base_url,
        timeout=config.timeout,
        provider_prefs=PROVIDER_PREFS,
    )
    cost = CostAccumulator()

    agents = [
        OpenRouterAgent(model_id=model, transport=transport, config=config, cost=cost)
        for model in ROSTER
    ]

    game_config = GameConfig(
        num_players=len(ROSTER),
        num_werewolves=NUM_WEREWOLVES,
        num_seers=NUM_SEERS,
        num_healers=NUM_HEALERS,
    )
    runner = MatchRunner(game_config, discussion_rounds=DISCUSSION_ROUNDS)
    state = runner.run(game_id=f"match-{seed}", seed=seed, agents=agents)

    record = state.to_record()
    record.cost = cost.to_game_cost()
    record.model_costs = cost.model_game_costs()
    record.prompt_version = PROMPT_VERSION
    record.season_id = SEASON_ID
    scoring.assess_void(record)

    print("\n--- seating (revealed at end) ---")
    for seat_id in sorted(record.seat_models):
        role = record.seat_roles[seat_id]
        alive = "alive" if seat_id in record.result.surviving_seat_ids else "dead"
        print(f"  Player {seat_id}: {record.seat_models[seat_id]:<40} {role:<9} {alive}")

    print("\n--- transcript ---")
    for event in state.events:
        print("  " + event.detail)

    print("\n--- result ---")
    print(f"  winner: {record.result.winner.value}")
    print(f"  rounds: {record.result.rounds_played}")
    print(f"  prompt: {PROMPT_VERSION}  season: {SEASON_ID}")

    # Per-model outcome summary: surfaces which models refused, emitted malformed
    # output, or errored (so provider-neutrality problems are obvious).
    print("\n--- per-model outcomes ---")
    per_model: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    notes: dict[str, str] = {}
    for action in record.actions:
        model = record.seat_models[action.seat_id]
        per_model[model][action.outcome.value] += 1
        if action.outcome is not ActionOutcome.ACCEPTED and action.note:
            notes.setdefault(model, action.note)
    for model in ROSTER:
        counts = per_model.get(model)
        if not counts:
            print(f"  {model:<40} (never acted)")
            continue
        summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        line = f"  {model:<40} {summary}"
        if model in notes:
            line += f"   e.g. {notes[model][:80]}"
        print(line)

    print("\n--- latency (slowest first) ---")
    ranked = sorted(
        ((s.avg_latency_s, m) for m, s in cost.per_model.items() if s.calls),
        reverse=True,
    )
    for avg, model in ranked:
        print(f"  {avg:5.1f}s/call   {model}")

    # Which upstream providers served calls, how fast, and what outcomes.
    print("\n--- by provider ---")
    prov_outcomes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for action in record.actions:
        prov_outcomes[action.provider or "(unknown)"][action.outcome.value] += 1
    for provider, stats in sorted(
        cost.per_provider.items(), key=lambda kv: kv[1].avg_latency_s, reverse=True
    ):
        oc = prov_outcomes.get(provider, {})
        oc_str = ", ".join(f"{k}={v}" for k, v in sorted(oc.items())) or "-"
        print(f"  {provider:<22} {stats.calls:>3} calls, {stats.avg_latency_s:5.1f}s/call   {oc_str}")

    c = record.cost
    print("\n--- cost ---")
    print(f"  calls:         {c.calls}")
    print(f"  input tokens:  {c.input_tokens}")
    print(f"  output tokens: {c.output_tokens}")
    if cost.price_observed:
        print(f"  total cost:    {c.total_cost:.6f} {c.currency}")
    else:
        print("  total cost:    unknown (no provider returned a price this run)")

    saved = save_game(record)
    print(f"\nSaved record + transcript: {saved}  (and {saved.with_suffix('.txt').name})")


if __name__ == "__main__":
    seed_arg = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    main(seed_arg)
