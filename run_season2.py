"""Season 2 scheduler: one model, nine provider/quant routes, pair-matched.

The quantization/provider study (see season2_config.py). Reuses Season 1's
hardening verbatim — per-game watchdog, auto-retry passes, resumability, void
detection, low concurrency — because the operational reality (provider throttle,
trickling upstreams, long unattended runs) is identical. The ONLY difference
from run_season.py is what sits in each seat: nine routes of ONE model, built
through the decoupled adapter (display label vs api model id + per-seat routing).

Pairs: with 9 seats / 2 wolves, C(9,2)=36 distinct wolf pairs. Pairing still
matters even in self-play: it balances how often each provider·quant sits wolf
vs village, so the quality metrics (malformed/illegal/dead-vote by route) are
read over a balanced sample, not a lopsided one.

OPERATIONAL (from S1, applies here): run in a NORMAL terminal, not the harness
background (it reaps processes at ~30 min). `python -u run_season2.py <N>` prints
per-game live and is resumable.

Run:  python -u run_season2.py [num_games]   (108 = each of 36 pairs x3)
"""

from __future__ import annotations

import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import combinations
from pathlib import Path

import scoring
import storage
from adapter.agent import OpenRouterAgent
from adapter.config import AdapterConfig
from adapter.openrouter import CostAccumulator, OpenRouterClient
from adapter.prompt import PROMPT_VERSION
from engine.game import GameConfig
from engine.runner import MatchRunner
from engine.types import Team
from season2_config import (DISCUSSION_ROUNDS, NUM_HEALERS, NUM_SEERS,
                            NUM_WEREWOLVES, SEASON_ID, SEATS)

CONCURRENCY = 1        # ONE at a time. A 4-wide burst probe 429'd zero times, but
                       # SUSTAINED 2-wide load soft-throttled every host: per-call
                       # latency jumped ~4s -> 13-20s and a long game blew the cap.
                       # No 429s — providers just slow responses under concurrent
                       # load from one account (the S1 lesson). Lower concurrency =
                       # faster per call + far fewer abandons, even with no overlap.
GAME_TIMEOUT = 2400    # seconds (40 min) per game before the watchdog abandons it.
                       # Raised 1200->1800->2400: at concurrency 1 a ~79-call game
                       # runs serially and rate-limit backoff stretches it. Runs are
                       # stable but slow, so the cap is set generously to abandon only
                       # genuinely STUCK games, never slow-but-progressing ones.
MAX_ATTEMPTS = 3       # retry passes for games that time out / abandon.
# Per-call transport tuning for flaky/rate-limiting hosts (e.g. WandB 429s after a
# handful of calls). More retries with exponential backoff lets a 429'd call WAIT
# and recover into a real turn instead of abstaining; the backoff also paces load
# back under the host's limit. Terminal 4xx (403 key-limit) still fail fast, so
# this can't reintroduce the old retry-on-403 hang. A tighter per-call timeout
# makes a genuinely hung upstream fail faster so it can't eat the game clock.
CALL_TIMEOUT = 30      # seconds per call (was 45); healthy gemma turns are a few s
CALL_RETRIES = 4       # attempts after the first (was 1) — recovers transient 429s
CALL_BACKOFF = 2.0     # backoff base: 2,4,8,16s between attempts (was 1)
ALL_PAIRS = list(combinations(range(len(SEATS)), 2))
random.Random(2026).shuffle(ALL_PAIRS)   # de-skew partial runs; fixed seed = stable schedule

_short = lambda label: label.split("@")[-1]   # the route is the interesting bit


def play_one(game_no, pair, config, transport, runner):
    """Play one game inside a watchdog daemon thread with a hard time cap.
    Returns (game_no, pair, record_or_None, elapsed_s, status)."""
    seat_order = SEATS[:]
    random.Random(game_no).shuffle(seat_order)
    a, b = SEATS[pair[0]], SEATS[pair[1]]
    wolf_seats = [seat_order.index(a), seat_order.index(b)]
    cost = CostAccumulator()
    # Each seat is the SAME model on a DIFFERENT route. The label is the seat's
    # identity (-> seat_models -> scoring/site); api_model_id + routing make the call.
    agents = [OpenRouterAgent(model_id=s.label, transport=transport, config=config,
                              cost=cost, api_model_id=s.api_model_id, routing=s.routing)
              for s in seat_order]
    box = {}

    def _play():
        try:
            state = runner.run(game_id=f"s2-{game_no:03d}", seed=game_no,
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
    # Per-provider avg latency this game (spots a slow/degrading host live, and
    # confirms no host is throttling under concurrency). Empty if nothing ran.
    prov = {p: round(s.avg_latency_s, 1) for p, s in cost.per_provider.items() if s.calls}
    if th.is_alive():
        return game_no, pair, None, elapsed, "timeout", prov
    if "error" in box:
        return game_no, pair, None, elapsed, box["error"], prov
    return game_no, pair, box["record"], elapsed, "ok", prov


def main(num_games: int) -> None:
    config = AdapterConfig.from_env()
    # No global provider_prefs: every route is supplied per-seat (the S2 seam).
    transport = OpenRouterClient(api_key=config.api_key, base_url=config.base_url,
                                 timeout=CALL_TIMEOUT, max_retries=CALL_RETRIES,
                                 backoff_base=CALL_BACKOFF)
    runner = MatchRunner(
        GameConfig(num_players=len(SEATS), num_werewolves=NUM_WEREWOLVES,
                   num_seers=NUM_SEERS, num_healers=NUM_HEALERS),
        discussion_rounds=DISCUSSION_ROUNDS,
    )
    season_dir = Path(storage.DEFAULT_DIR) / SEASON_ID
    n_pairs = len(ALL_PAIRS)
    target = [(n, ALL_PAIRS[(n - 1) % n_pairs]) for n in range(1, num_games + 1)]

    print(f"Season 2 (quant study): {num_games} games = each of {n_pairs} pairs "
          f"x{num_games // n_pairs}. Model {SEATS[0].api_model_id}, 9 routes "
          f"(per-game cap {GAME_TIMEOUT}s, up to {MAX_ATTEMPTS} retry passes).\n", flush=True)

    wins = {Team.WEREWOLVES: 0, Team.VILLAGERS: 0}
    total_cost = 0.0
    voids = 0

    for attempt in range(1, MAX_ATTEMPTS + 1):
        existing = {p.stem for p in season_dir.glob("*.json")} if season_dir.exists() else set()
        todo = [(n, p) for n, p in target if f"s2-{n:03d}" not in existing]
        if not todo:
            break
        print(f"--- pass {attempt}: {len(existing)} saved, {len(todo)} to run ---", flush=True)
        with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
            futs = {pool.submit(play_one, n, p, config, transport, runner): n for n, p in todo}
            for fut in as_completed(futs):
                gno, pair, rec, elapsed, status, prov = fut.result()
                provstr = " ".join(f"{p}:{s}s" for p, s in sorted(prov.items(), key=lambda kv: -kv[1]))
                if rec is None:
                    print(f"  g{gno:03d}: ABANDONED [{status}] {elapsed:.0f}s  {provstr}", flush=True)
                    continue
                storage.save_game(rec)
                wins[rec.result.winner] += 1
                total_cost += rec.cost.total_cost
                voids += int(rec.void)
                fails = sum(1 for a in rec.actions
                            if a.outcome.value in ("timeout", "malformed", "illegal"))
                vtag = " VOID" if rec.void else ""
                print(f"  g{gno:03d} [{_short(SEATS[pair[0]].label)}+{_short(SEATS[pair[1]].label)}] "
                      f"-> {rec.result.winner.value:<10} {rec.result.rounds_played}r "
                      f"{elapsed:.0f}s ${rec.cost.total_cost:.4f} {fails}f{vtag}  {provstr}", flush=True)

    saved = len({p.stem for p in season_dir.glob("*.json")}) if season_dir.exists() else 0
    print(f"\n=== this run: wolves {wins[Team.WEREWOLVES]} / villagers {wins[Team.VILLAGERS]} "
          f"· {voids} void · ${total_cost:.4f} ===", flush=True)
    print(f"season-2 total saved: {saved}/{num_games}", flush=True)
    if saved < num_games:
        print(f"{num_games - saved} still missing after {MAX_ATTEMPTS} passes — re-run to retry.",
              flush=True)


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 108
    main(n)
