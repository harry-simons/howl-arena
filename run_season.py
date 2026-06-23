"""Season scheduler: pair-matched wolf seating, hardened for long unattended runs.

Every model co-wolfs with every other. With 9 models / 2 wolves that is
C(9,2)=36 distinct pairs; pass num_games as a multiple of 36 to play each pair
that many times (e.g. 108 = every pair x3). Pairs are cycled (game n -> pair
(n-1) % 36) with a shuffled, per-game seating, each game its own seed.

Robustness (so a 100+ game run completes unattended):
- PER-GAME TIMEOUT: each game runs in a daemon watchdog thread with a hard wall-
  clock cap. If it exceeds GAME_TIMEOUT it is abandoned (NOT saved) and its pool
  slot frees immediately — a single stuck/trickling provider call can't block the
  run. The leaked thread is a daemon, so it never blocks process exit.
- AUTO-RETRY: the run loops up to MAX_ATTEMPTS, each pass re-running only the games
  still missing (resumable by saved file). Abandoned games get another shot.
- VOID detection: degenerate games are flagged (excluded from ratings).
- Per-game wall-clock is logged.

Run:  python -u run_season.py [num_games]   (108 = full 3x coverage)
"""

from __future__ import annotations

import random
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import combinations
from pathlib import Path

import scoring
import storage
from adapter.agent import OpenRouterAgent
from adapter.config import AdapterConfig, PROVIDER_PREFS
from adapter.openrouter import CostAccumulator, OpenRouterClient
from adapter.prompt import PROMPT_VERSION
from engine.game import GameConfig
from engine.runner import MatchRunner
from engine.types import Team
from run_match import (DISCUSSION_ROUNDS, NUM_HEALERS, NUM_SEERS, NUM_WEREWOLVES,
                       ROSTER, SEASON_ID)

CONCURRENCY = 2        # dropped from 5: sustained 5-wide load throttled providers,
                       # making games slow + abandon. Fewer parallel calls = faster
                       # per-game responses and far fewer timeouts.
GAME_TIMEOUT = 1200    # seconds (20 min); raised from 600 — at 600 only ~1/5 games finished
                       # (slow reasoning models + provider throttling), so most were abandoned
MAX_ATTEMPTS = 3       # retry passes for games that time out / abandon
ALL_PAIRS = list(combinations(range(len(ROSTER)), 2))
random.Random(2026).shuffle(ALL_PAIRS)   # de-skew partial runs; fixed seed = stable schedule

_short = lambda m: m.split("/")[-1]


def play_one(game_no, pair, config, transport, runner):
    """Play one game inside a watchdog daemon thread with a hard time cap.
    Returns (game_no, pair, record_or_None, elapsed_s, status)."""
    a, b = ROSTER[pair[0]], ROSTER[pair[1]]
    seat_order = ROSTER[:]
    random.Random(game_no).shuffle(seat_order)
    wolf_seats = [seat_order.index(a), seat_order.index(b)]
    cost = CostAccumulator()
    agents = [OpenRouterAgent(model_id=m, transport=transport, config=config, cost=cost)
              for m in seat_order]
    box = {}

    def _play():
        try:
            state = runner.run(game_id=f"s1-{game_no:03d}", seed=game_no,
                               agents=agents, wolf_seats=wolf_seats)
            rec = state.to_record()
            rec.cost = cost.to_game_cost()
            rec.model_costs = cost.model_game_costs()
            rec.prompt_version = PROMPT_VERSION
            rec.season_id = SEASON_ID
            scoring.assess_void(rec)
            box["record"] = rec
        except Exception as exc:           # never let one game crash the worker
            box["error"] = f"{type(exc).__name__}: {exc}"

    t0 = time.monotonic()
    th = threading.Thread(target=_play, daemon=True)
    th.start()
    th.join(GAME_TIMEOUT)
    elapsed = time.monotonic() - t0
    if th.is_alive():
        return game_no, pair, None, elapsed, "timeout"
    if "error" in box:
        return game_no, pair, None, elapsed, box["error"]
    return game_no, pair, box["record"], elapsed, "ok"


def main(num_games: int) -> None:
    config = AdapterConfig.from_env()
    transport = OpenRouterClient(api_key=config.api_key, base_url=config.base_url,
                                 timeout=config.timeout, max_retries=1,
                                 provider_prefs=PROVIDER_PREFS)
    runner = MatchRunner(
        GameConfig(num_players=len(ROSTER), num_werewolves=NUM_WEREWOLVES,
                   num_seers=NUM_SEERS, num_healers=NUM_HEALERS),
        discussion_rounds=DISCUSSION_ROUNDS,
    )
    season_dir = Path(storage.DEFAULT_DIR) / SEASON_ID
    n_pairs = len(ALL_PAIRS)
    target = [(n, ALL_PAIRS[(n - 1) % n_pairs]) for n in range(1, num_games + 1)]

    print(f"Season 1: {num_games} games = each of {n_pairs} pairs x{num_games // n_pairs} "
          f"(per-game cap {GAME_TIMEOUT}s, up to {MAX_ATTEMPTS} retry passes).\n", flush=True)

    wins = {Team.WEREWOLVES: 0, Team.VILLAGERS: 0}
    total_cost = 0.0
    voids = 0

    for attempt in range(1, MAX_ATTEMPTS + 1):
        existing = {p.stem for p in season_dir.glob("*.json")} if season_dir.exists() else set()
        todo = [(n, p) for n, p in target if f"s1-{n:03d}" not in existing]
        if not todo:
            break
        print(f"--- pass {attempt}: {len(existing)} saved, {len(todo)} to run ---", flush=True)
        with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
            futs = {pool.submit(play_one, n, p, config, transport, runner): n for n, p in todo}
            for fut in as_completed(futs):
                gno, pair, rec, elapsed, status = fut.result()
                if rec is None:
                    print(f"  g{gno:03d}: ABANDONED [{status}] {elapsed:.0f}s", flush=True)
                    continue
                storage.save_game(rec)
                wins[rec.result.winner] += 1
                total_cost += rec.cost.total_cost
                voids += int(rec.void)
                fails = sum(1 for a in rec.actions
                            if a.outcome.value in ("timeout", "malformed", "illegal"))
                vtag = " VOID" if rec.void else ""
                print(f"  g{gno:03d} [{_short(ROSTER[pair[0]])}+{_short(ROSTER[pair[1]])}] "
                      f"-> {rec.result.winner.value:<10} {rec.result.rounds_played}r "
                      f"{elapsed:.0f}s ${rec.cost.total_cost:.4f} {fails}f{vtag}", flush=True)

    saved = len({p.stem for p in season_dir.glob("*.json")}) if season_dir.exists() else 0
    print(f"\n=== this run: wolves {wins[Team.WEREWOLVES]} / villagers {wins[Team.VILLAGERS]} "
          f"· {voids} void · ${total_cost:.4f} ===", flush=True)
    print(f"season-1 total saved: {saved}/{num_games}", flush=True)
    if saved < num_games:
        print(f"{num_games - saved} still missing after {MAX_ATTEMPTS} passes — re-run to retry.",
              flush=True)


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 108
    main(n)
