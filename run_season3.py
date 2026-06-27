"""Season 3 scheduler: one model, one route, four prompt variants, pair-matched.

The prompt-engineering study (see season3_config.py). Reuses Season 1/2's
hardening verbatim — per-game watchdog, auto-retry passes, resumability, void
detection, low concurrency — because the operational reality (provider throttle,
trickling upstreams, long unattended runs) is identical. The ONLY difference
from run_season2.py is what varies between seats: nine seats of ONE model on ONE
route, each carrying a different system-prompt VARIANT (built through the
decoupled adapter — display label + per-seat prompt variant). All nine share the
same Novita bf16 route, so the prompt is the only variable.

Pairs: with 9 seats / 2 wolves, C(9,2)=36 distinct wolf pairs. Pairing balances
how often each variant sits wolf vs village, so the win-rate and quality metrics
are read over a balanced sample, not a lopsided one. Unlike Season 2, win-rate
IS a headline here — the prompts genuinely differ, so a better prompt should win
more across the balanced schedule.

OPERATIONAL (from S1, applies here): run in a NORMAL terminal, not the harness
background (it reaps processes at ~30 min). `python -u run_season3.py <N>` prints
per-game live and is resumable.

Run:  python -u run_season3.py [num_games]   (default 72 = each of 36 pairs x2)

Default is 72, not 108: with only FOUR distinct prompt variants behind the nine
seats (baseline x3, coached/cot/statetrack x2 each), full x3 distinct-pair
coverage is unnecessary — the variant-level sample size is what matters, and 72
(two balanced pair-cycles, every seat wolf exactly 16x) gives ~±12pt win-rate
resolution vs baseline plus well-powered quality signals. Pass 108 to go further.
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
from engine.game import GameConfig
from engine.runner import MatchRunner
from engine.types import Team
from season3_config import (DISCUSSION_ROUNDS, NUM_HEALERS, NUM_SEERS,
                            NUM_WEREWOLVES, PROMPT_VERSION, SEASON_ID, SEATS)

CONCURRENCY = 1        # ONE at a time. Every seat now hits the SAME host (Novita
                       # bf16), so concurrency would concentrate load on one
                       # upstream — exactly the throttle pattern S1/S2 hit. Serial
                       # is faster per call and avoids the soft-throttle slowdown.
GAME_TIMEOUT = 4800    # seconds (80 min) per game before the watchdog abandons it.
                       # Generous cap (per S2): abandon only genuinely hung games,
                       # not slow-but-progressing ones. Per-call timeout still
                       # kills a truly dead call fast.
MAX_ATTEMPTS = 3       # retry passes for games that time out / abandon.
# Per-call transport tuning (from S2): more retries with exponential backoff lets
# a 429'd call WAIT and recover into a real turn instead of abstaining, and the
# backoff paces load back under the host's limit. Terminal 4xx (403 key-limit)
# still fail fast, so this can't reintroduce the old retry-on-403 hang.
CALL_TIMEOUT = 60      # seconds per call. Raised 30->60 for S3: the statetrack
                       # variant writes a full per-player ledger table every turn
                       # (verify probe took 16s even at a 400-token cap), so at the
                       # 2000-token play cap a real statetrack turn can run 20-40s.
                       # A 30s cap would time those out and abstain them, turning a
                       # token-budget artifact into a fake play-quality signal in the
                       # very variant we most want to read cleanly. 60s gives the
                       # verbose variants room; the 80-min GAME_TIMEOUT still backstops
                       # a genuinely hung call, and concurrency=1 means one slow call
                       # only slows its own game.
CALL_RETRIES = 4       # attempts after the first — recovers transient 429s
CALL_BACKOFF = 2.0     # backoff base: 2,4,8,16s between attempts
ALL_PAIRS = list(combinations(range(len(SEATS)), 2))
random.Random(2026).shuffle(ALL_PAIRS)   # de-skew partial runs; fixed seed = stable schedule

_short = lambda label: label.split("@")[-1]   # the variant is the interesting bit


def play_one(game_no, pair, config, transport, runner):
    """Play one game inside a watchdog daemon thread with a hard time cap.
    Returns (game_no, pair, record_or_None, elapsed_s, status, prov)."""
    seat_order = SEATS[:]
    random.Random(game_no).shuffle(seat_order)
    a, b = SEATS[pair[0]], SEATS[pair[1]]
    wolf_seats = [seat_order.index(a), seat_order.index(b)]
    cost = CostAccumulator()
    # Each seat is the SAME model on the SAME route, carrying a DIFFERENT prompt
    # variant. The label is the seat's identity (-> seat_models -> scoring/site);
    # api_model_id + routing make the call; prompt_variant is the only variable.
    agents = [OpenRouterAgent(model_id=s.label, transport=transport, config=config,
                              cost=cost, api_model_id=s.api_model_id,
                              routing=s.routing, prompt_variant=s.variant)
              for s in seat_order]
    box = {}

    def _play():
        try:
            state = runner.run(game_id=f"s3-{game_no:03d}", seed=game_no,
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
    # Per-provider avg latency this game (all seats are on Novita, so this is a
    # single-host health read — spots a slow/degrading Novita live).
    prov = {p: round(s.avg_latency_s, 1) for p, s in cost.per_provider.items() if s.calls}
    if th.is_alive():
        return game_no, pair, None, elapsed, "timeout", prov
    if "error" in box:
        return game_no, pair, None, elapsed, box["error"], prov
    return game_no, pair, box["record"], elapsed, "ok", prov


def main(num_games: int) -> None:
    config = AdapterConfig.from_env()
    # No global provider_prefs: the (shared) route is supplied per-seat.
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

    print(f"Season 3 (prompt study): {num_games} games = each of {n_pairs} pairs "
          f"x{num_games // n_pairs}. Model {SEATS[0].api_model_id} on Novita bf16, "
          f"9 prompt variants (per-game cap {GAME_TIMEOUT}s, up to {MAX_ATTEMPTS} "
          f"retry passes).\n", flush=True)

    wins = {Team.WEREWOLVES: 0, Team.VILLAGERS: 0}
    total_cost = 0.0
    voids = 0

    for attempt in range(1, MAX_ATTEMPTS + 1):
        existing = {p.stem for p in season_dir.glob("*.json")} if season_dir.exists() else set()
        todo = [(n, p) for n, p in target if f"s3-{n:03d}" not in existing]
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
    print(f"season-3 total saved: {saved}/{num_games}", flush=True)
    if saved < num_games:
        print(f"{num_games - saved} still missing after {MAX_ATTEMPTS} passes — re-run to retry.",
              flush=True)


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 72
    main(n)
