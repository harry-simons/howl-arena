"""Season 3 analysis — the prompt-engineering study.

Season 3 holds ONE model (gemma-4-31b-it) AND one route (Novita bf16) fixed and
varies ONLY the system prompt across the nine seats: baseline x3, coached x2,
cot x2, statetrack x2. Unlike Season 2 (symmetric self-play, win-rate uninforma-
tive), the prompts genuinely differ, so WIN-RATE is a headline again — alongside
the directed quality hypotheses: does the coaching primer lift play? does the
state-tracking ledger actually cut dead-player votes? does chain-of-thought cut
illegal moves?

Reads games/season-3/*.json and computes, per variant and per replica seat:
win-rates (overall / village / wolf), the dead-vote rate (the key directed
metric), format quality (malformed / illegal), role ratings (pooled re-score,
the same Glicko/Elo/TrueSkill engine as the site), and the replica NOISE FLOOR
(identical prompts run 2-3x — their spread is the error bar any real between-
variant gap must clear). Writes analysis/out/season3.json for site_analysis.

Pure data analysis: no model/API calls.
"""
from __future__ import annotations

import glob
import json
import os
import sys
from collections import defaultdict

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PKG)
import scoring  # noqa: E402
import season3_config  # noqa: E402  (the prompt texts — single source of truth)

SEASON_DIR = os.path.join(PKG, "games", "season-3")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "season3.json")

VARIANT_ORDER = {"baseline": 0, "coached": 1, "cot": 2, "statetrack": 3}


def load():
    games = []
    for f in sorted(glob.glob(os.path.join(SEASON_DIR, "*.json"))):
        with open(f, encoding="utf-8") as fh:
            games.append(json.load(fh))
    return games


def variant_of(label: str) -> str:
    """'gemma-4-31b@coached-r2' -> 'coached' (replica tag stripped)."""
    after = label.split("@", 1)[1]
    return after.split("-r")[0]


def main():
    games = load()
    n = len(games)
    if not n:
        print("no season-3 games yet")
        return

    seat = defaultdict(lambda: {
        "actions": 0, "malformed": 0, "illegal": 0, "timeout": 0, "refused": 0,
        "votes": 0, "dead_votes": 0, "games": 0,
        "wolf_games": 0, "village_games": 0, "wolf_wins": 0, "village_wins": 0,
        "cost": 0.0,
    })
    wins = {"werewolves": 0, "villagers": 0}
    total_cost = 0.0

    for rec in games:
        if rec.get("void"):
            continue
        sm = {int(k): v for k, v in rec.get("seat_models", {}).items()}
        sr = {int(k): v for k, v in rec.get("seat_roles", {}).items()}
        winner = (rec.get("result") or {}).get("winner")
        wins[winner] = wins.get(winner, 0) + 1
        total_cost += (rec.get("cost") or {}).get("total_cost", 0.0)
        for s, label in sm.items():
            d = seat[label]
            d["games"] += 1
            if sr.get(s) == "werewolf":
                d["wolf_games"] += 1
                d["wolf_wins"] += int(winner == "werewolves")
            else:
                d["village_games"] += 1
                d["village_wins"] += int(winner == "villagers")
        for a in rec.get("actions", []):
            label = sm.get(a.get("seat_id"))
            if not label:
                continue
            d = seat[label]
            d["actions"] += 1
            oc = a.get("outcome")
            if oc in ("malformed", "illegal", "timeout", "refused"):
                d[oc] += 1
            if a.get("action_type") == "vote":
                d["votes"] += 1
                if oc == "illegal" and str(a.get("note", "")).startswith("target_dead"):
                    d["dead_votes"] += 1
        for label, c in (rec.get("model_costs") or {}).items():
            seat[label]["cost"] += c.get("total_cost", 0.0)

    # per-seat ratings (role-conditional Glicko + Elo + TrueSkill, the site engine)
    ratings = scoring.score_games(games)

    # "Average player" per VARIANT: relabel every replica seat to its variant name
    # and re-score, so each variant gets ONE rating over 2-3x the appearances
    # (tighter than averaging noisy replica ratings). The headline comparison.
    relabeled = []
    for rec in games:
        r = dict(rec)
        sm = {int(k): v for k, v in rec.get("seat_models", {}).items()}
        r["seat_models"] = {str(s): variant_of(lbl) for s, lbl in sm.items()}
        relabeled.append(r)
    vr = scoring.score_games(relabeled)

    def seat_rate(d):
        a = d["actions"] or 1
        v = d["votes"] or 1
        return {
            "games": d["games"], "actions": d["actions"],
            "win_pct": round(100 * (d["wolf_wins"] + d["village_wins"]) / d["games"], 1) if d["games"] else 0,
            "wolf_wr": round(100 * d["wolf_wins"] / d["wolf_games"], 1) if d["wolf_games"] else None,
            "village_wr": round(100 * d["village_wins"] / d["village_games"], 1) if d["village_games"] else None,
            "dead_votes": d["dead_votes"], "votes": d["votes"],
            "dead_vote_pct": round(100 * d["dead_votes"] / v, 1),
            "malformed_pct": round(100 * d["malformed"] / a, 2),
            "illegal_pct": round(100 * d["illegal"] / a, 2),
        }

    seats_out = {}
    for label, d in seat.items():
        rt = ratings.get(label, {})
        seats_out[label] = {
            "variant": variant_of(label),
            **seat_rate(d),
            "overall_rating": round(rt.get("overall", {}).get("rating", 0)),
            "wolf_rating": round(rt.get("wolf", {}).get("rating", 0)),
            "villager_rating": round(rt.get("villager", {}).get("rating", 0)),
            "elo": round(rt.get("elo", {}).get("rating", 0)),
            "trueskill": round(rt.get("trueskill", {}).get("rating", 0), 1),
        }

    # ---- per-variant aggregate (pool the replicas) ------------------------
    pervar = {}
    for v in VARIANT_ORDER:
        labels = [l for l in seat if variant_of(l) == v]
        if not labels:
            continue
        agg = defaultdict(int)
        cost = 0.0
        for l in labels:
            for k in ("actions", "malformed", "illegal", "timeout", "votes",
                      "dead_votes", "games", "wolf_games", "village_games",
                      "wolf_wins", "village_wins"):
                agg[k] += seat[l][k]
            cost += seat[l]["cost"]
        a = agg["actions"] or 1
        vt = agg["votes"] or 1
        rr = vr.get(v, {})
        # replica spread = the noise floor for this variant
        ov = [seats_out[l]["overall_rating"] for l in labels]
        wins_pct = [seats_out[l]["win_pct"] for l in labels]
        dead = [seats_out[l]["dead_vote_pct"] for l in labels]
        pervar[v] = {
            "replicas": sorted(labels),
            "n_replicas": len(labels),
            "games": agg["games"],
            "win_pct": round(100 * (agg["wolf_wins"] + agg["village_wins"]) / agg["games"], 1) if agg["games"] else 0,
            "village_wr": round(100 * agg["village_wins"] / agg["village_games"], 1) if agg["village_games"] else None,
            "wolf_wr": round(100 * agg["wolf_wins"] / agg["wolf_games"], 1) if agg["wolf_games"] else None,
            "votes": agg["votes"], "dead_votes": agg["dead_votes"],
            "dead_vote_pct": round(100 * agg["dead_votes"] / vt, 1),
            "actions": agg["actions"],
            "malformed_pct": round(100 * agg["malformed"] / a, 2),
            "illegal_pct": round(100 * agg["illegal"] / a, 2),
            "cost": round(cost, 4),
            # pooled re-score ratings
            "overall_rating": round(rr.get("overall", {}).get("rating", 0)),
            "overall_provisional": rr.get("overall", {}).get("provisional", True),
            "wolf_rating": round(rr.get("wolf", {}).get("rating", 0)),
            "wolf_rd": round(rr.get("wolf", {}).get("rd", 0)),
            "villager_rating": round(rr.get("villager", {}).get("rating", 0)),
            "villager_rd": round(rr.get("villager", {}).get("rd", 0)),
            "elo": round(rr.get("elo", {}).get("rating", 0)),
            "trueskill": round(rr.get("trueskill", {}).get("rating", 0), 1),
            "noise_floor": {
                "overall_rating_range": [min(ov), max(ov)],
                "overall_rating_spread": max(ov) - min(ov),
                "win_pct_range": [min(wins_pct), max(wins_pct)],
                "win_pct_spread": round(max(wins_pct) - min(wins_pct), 1),
                "dead_vote_pct_range": [min(dead), max(dead)],
            },
        }

    base_nf = pervar.get("baseline", {}).get("noise_floor", {})

    out = {
        "season": "season-3",
        "n_games": n,
        "model": "google/gemma-4-31b-it",
        "route": "Novita bf16",
        "wins": wins,
        "total_cost": round(total_cost, 4),
        "cost_per_game": round(total_cost / n, 4) if n else 0,
        "per_variant": pervar,
        "seats": seats_out,
        "baseline_noise_floor": base_nf,
        # the actual prompt text per variant (None = baseline/control) + the shared
        # base prompt, pulled live from season3_config so the site can show exactly
        # what each variant instructs.
        "prompts": {v: season3_config.GUIDANCE.get(v) for v in VARIANT_ORDER},
        "base_system": season3_config.VARIANTS["baseline"].system,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)

    # ---- console summary --------------------------------------------------
    print(f"Season 3: {n} games, ${total_cost:.2f} (${total_cost/n:.4f}/game)")
    print(f"wins: wolves {wins.get('werewolves',0)} / villagers {wins.get('villagers',0)}\n")
    print("POOLED BY VARIANT (replicas re-scored as one player):")
    print(f"  {'variant':<11}{'win%':>6}{'vil%':>6}{'wolf%':>6}{'dead-vote%':>11}{'illegal%':>9}{'overall':>8}{'elo':>6}{'TS':>7}")
    for v in sorted(pervar, key=lambda k: VARIANT_ORDER[k]):
        d = pervar[v]
        print(f"  {v:<11}{d['win_pct']:>6}{str(d['village_wr']):>6}{str(d['wolf_wr']):>6}"
              f"{d['dead_vote_pct']:>11}{d['illegal_pct']:>9}{d['overall_rating']:>8}{d['elo']:>6}{d['trueskill']:>7}")
    nf = base_nf
    print(f"\nBASELINE NOISE FLOOR (identical prompt, {pervar.get('baseline',{}).get('n_replicas','?')} replicas):")
    print(f"  overall-rating spread {nf.get('overall_rating_spread')}  "
          f"win% range {nf.get('win_pct_range')}  dead-vote% range {nf.get('dead_vote_pct_range')}")
    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()
