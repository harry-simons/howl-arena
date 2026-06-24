"""Season 2 analysis — the quantization study.

Season 2 holds ONE model (gemma-4-31b-it) fixed and seats it nine ways across a
precision ladder (bf16 x3 / fp8 x3 / fp4 x3, on Novita + DeepInfra). Win-rate is
NOT the headline here: it is self-play, so outcomes are symmetric and noisy. The
headline is SERVING QUALITY by precision — does the same model, served at a lower
quant, break the format more (malformed / illegal / dead-votes) or play worse?

This is a different question from Season 1's model ladder, so it gets its own
computation rather than reusing the S1 scripts. Reads games/season-2/*.json,
computes per-route and per-quant quality, the replica noise floor (identical
routes run 3x — their spread is the error bar), the within-host DeepInfra
fp8-vs-fp4 isolation, role ratings, and cost. Writes analysis/out/season2.json
for site_analysis.build to reshape onto the site.

Pure data analysis: no model/API calls.
"""
from __future__ import annotations

import glob
import json
import os
import statistics
from collections import defaultdict

import sys
PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PKG)
import scoring  # noqa: E402

SEASON_DIR = os.path.join(PKG, "games", "season-2")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "season2.json")

QUANT_ORDER = {"bf16": 0, "fp8": 1, "fp4": 2}


def load():
    games = []
    for f in sorted(glob.glob(os.path.join(SEASON_DIR, "*.json"))):
        with open(f, encoding="utf-8") as fh:
            games.append(json.load(fh))
    return games


def parse_label(label: str):
    """'gemma-4-31b@DeepInfra-fp4-r2' -> (host, quant, replica_tag)."""
    after = label.split("@", 1)[1]
    parts = after.split("-")
    host, quant = parts[0], parts[1]
    rep = parts[2] if len(parts) > 2 else "r1"
    return host, quant, rep


def main():
    games = load()
    n = len(games)

    # ---- per-route quality + outcome tallies -----------------------------
    route = defaultdict(lambda: {
        "actions": 0, "malformed": 0, "illegal": 0, "timeout": 0, "refused": 0,
        "dead_votes": 0, "wolf_games": 0, "village_games": 0,
        "wolf_wins": 0, "village_wins": 0, "games": 0,
        # cost + "effective turn" accounting (does the cheap rate survive once we
        # only count legal, productive turns and normalise by seat-appearance?).
        "cost": 0.0, "in_tok": 0, "out_tok": 0,
        "effective": 0, "wasted": 0, "abstain": 0, "appear": 0, "survived": 0,
    })
    wins = {"werewolves": 0, "villagers": 0}
    total_cost = 0.0

    for rec in games:
        if rec.get("void"):
            continue
        sm = {int(k): v for k, v in rec.get("seat_models", {}).items()}
        sr = {int(k): v for k, v in rec.get("seat_roles", {}).items()}
        winner = (rec.get("result") or {}).get("winner")
        surv = set((rec.get("result") or {}).get("surviving_seat_ids", []))
        wins[winner] = wins.get(winner, 0) + 1
        total_cost += (rec.get("cost") or {}).get("total_cost", 0.0)
        for seat, label in sm.items():
            r = route[label]
            r["games"] += 1
            r["appear"] += 1
            r["survived"] += int(seat in surv)
            role = sr.get(seat)
            if role == "werewolf":
                r["wolf_games"] += 1
                r["wolf_wins"] += int(winner == "werewolves")
            else:
                r["village_games"] += 1
                r["village_wins"] += int(winner == "villagers")
        for a in rec.get("actions", []):
            label = sm.get(a.get("seat_id"))
            if not label:
                continue
            r = route[label]
            r["actions"] += 1
            oc = a.get("outcome")
            if oc in ("malformed", "illegal", "timeout", "refused"):
                r[oc] += 1
            if oc in ("malformed", "illegal", "timeout"):
                r["wasted"] += 1            # a turn we paid for that did nothing legal
            elif oc == "accepted":
                if a.get("action_type") == "abstain":
                    r["abstain"] += 1
                else:
                    r["effective"] += 1     # accepted, productive turn
            if oc == "illegal" and str(a.get("note", "")).startswith("target_dead"):
                r["dead_votes"] += 1
        for label, c in (rec.get("model_costs") or {}).items():
            r = route[label]
            r["cost"] += c.get("total_cost", 0.0)
            r["in_tok"] += c.get("input_tokens", 0)
            r["out_tok"] += c.get("output_tokens", 0)

    # ratings per route (role-conditional Glicko, the same engine as the site)
    ratings = scoring.score_games(games)

    # "Average player" per precision: relabel the three replicas at each quant to
    # a single identity ("bf16"/"fp8"/"fp4") and re-score, so each precision gets
    # ONE rating over 3x the appearances (tighter than averaging three noisy
    # route ratings). This is the headline comparison of the study.
    relabeled = []
    for rec in games:
        r = dict(rec)
        sm = {int(k): v for k, v in rec.get("seat_models", {}).items()}
        r["seat_models"] = {str(s): parse_label(lbl)[1] for s, lbl in sm.items()}
        relabeled.append(r)
    qr = scoring.score_games(relabeled)

    def _rate(block):
        return {"rating": round(block["rating"]), "rd": round(block["rd"]),
                "games": block["games"], "provisional": block["provisional"]}

    quant_ratings = {q: {
        "overall": round(qr[q]["overall"]["rating"]),
        "overall_provisional": qr[q]["overall"]["provisional"],
        "wolf": _rate(qr[q]["wolf"]),
        "villager": _rate(qr[q]["villager"]),
    } for q in ("bf16", "fp8", "fp4") if q in qr}

    def rates(d):
        a = d["actions"] or 1
        bad = d["malformed"] + d["illegal"] + d["timeout"]
        return {
            "actions": d["actions"],
            "malformed_pct": round(100 * d["malformed"] / a, 2),
            "illegal_pct": round(100 * d["illegal"] / a, 2),
            "dead_vote_pct": round(100 * d["dead_votes"] / a, 2),
            "timeout_pct": round(100 * d["timeout"] / a, 2),
            "bad_pct": round(100 * bad / a, 2),
            "malformed": d["malformed"], "illegal": d["illegal"],
            "dead_votes": d["dead_votes"], "timeout": d["timeout"],
        }

    routes_out = {}
    for label, d in route.items():
        host, quant, rep = parse_label(label)
        rt = ratings.get(label, {})
        routes_out[label] = {
            "host": host, "quant": quant, "replica": rep,
            **rates(d),
            "wolf_wr": round(d["wolf_wins"] / d["wolf_games"], 3) if d["wolf_games"] else None,
            "village_wr": round(d["village_wins"] / d["village_games"], 3) if d["village_games"] else None,
            "wolf_games": d["wolf_games"], "village_games": d["village_games"],
            "wolf_rating": round(rt.get("wolf", {}).get("rating", 0)),
            "villager_rating": round(rt.get("villager", {}).get("rating", 0)),
            "overall_rating": round(rt.get("overall", {}).get("rating", 0)),
            # cost + effective-turn economics
            "cost": round(d["cost"], 4),
            "effective": d["effective"],
            "wasted": d["wasted"],
            "appearances": d["appear"],
            "survival_pct": round(100 * d["survived"] / d["appear"], 1) if d["appear"] else 0,
            "turns_per_appearance": round(d["actions"] / d["appear"], 1) if d["appear"] else 0,
            "usd_per_effective": round(d["cost"] / d["effective"], 6) if d["effective"] else None,
            "usd_per_appearance": round(d["cost"] / d["appear"], 6) if d["appear"] else None,
            "eff_usd_per_m": round(d["cost"] / ((d["in_tok"] + d["out_tok"]) / 1e6), 4) if (d["in_tok"] + d["out_tok"]) else None,
        }

    # ---- per-quant aggregate (sum the 3 routes at each level) -------------
    perq = {}
    for q in ("bf16", "fp8", "fp4"):
        labels = [l for l in route if parse_label(l)[1] == q]
        agg = defaultdict(int)
        for l in labels:
            for k in ("actions", "malformed", "illegal", "timeout", "dead_votes",
                      "wolf_games", "village_games", "wolf_wins", "village_wins",
                      "effective", "wasted", "appear", "survived", "in_tok", "out_tok"):
                agg[k] += route[l][k]
            agg["cost"] += route[l]["cost"]
        a = agg["actions"] or 1
        # replica noise floor: spread of each route's key rates within this quant
        mal_rates = [routes_out[l]["malformed_pct"] for l in labels]
        ill_rates = [routes_out[l]["illegal_pct"] for l in labels]
        dead_rates = [routes_out[l]["dead_vote_pct"] for l in labels]
        ov_ratings = [routes_out[l]["overall_rating"] for l in labels]
        perq[q] = {
            "routes": sorted(labels),
            "actions": agg["actions"],
            "malformed_pct": round(100 * agg["malformed"] / a, 2),
            "illegal_pct": round(100 * agg["illegal"] / a, 2),
            "dead_vote_pct": round(100 * agg["dead_votes"] / a, 2),
            "bad_pct": round(100 * (agg["malformed"] + agg["illegal"] + agg["timeout"]) / a, 2),
            "wolf_wr": round(agg["wolf_wins"] / agg["wolf_games"], 3) if agg["wolf_games"] else None,
            "village_wr": round(agg["village_wins"] / agg["village_games"], 3) if agg["village_games"] else None,
            "cost": round(agg["cost"], 4),
            "effective": agg["effective"],
            "wasted": agg["wasted"],
            "waste_pct": round(100 * agg["wasted"] / a, 2),
            "appearances": agg["appear"],
            "survival_pct": round(100 * agg["survived"] / agg["appear"], 1) if agg["appear"] else 0,
            "turns_per_appearance": round(agg["actions"] / agg["appear"], 1) if agg["appear"] else 0,
            "usd_per_effective": round(agg["cost"] / agg["effective"], 6) if agg["effective"] else None,
            "usd_per_appearance": round(agg["cost"] / agg["appear"], 6) if agg["appear"] else None,
            "eff_usd_per_m": round(agg["cost"] / ((agg["in_tok"] + agg["out_tok"]) / 1e6), 4) if (agg["in_tok"] + agg["out_tok"]) else None,
            "noise_floor": {
                "malformed_pct_range": [min(mal_rates), max(mal_rates)],
                "illegal_pct_range": [min(ill_rates), max(ill_rates)],
                "dead_vote_pct_range": [min(dead_rates), max(dead_rates)],
                "overall_rating_range": [min(ov_ratings), max(ov_ratings)],
                "overall_rating_spread": max(ov_ratings) - min(ov_ratings),
            },
        }

    # ---- within-host isolation: DeepInfra fp8 vs fp4 (zero provider confound)
    def host_quant(host, quant):
        labels = [l for l in route if parse_label(l)[:2] == (host, quant)]
        agg = defaultdict(int)
        for l in labels:
            for k in ("actions", "malformed", "illegal", "timeout", "dead_votes"):
                agg[k] += route[l][k]
        a = agg["actions"] or 1
        return {"actions": agg["actions"],
                "malformed_pct": round(100 * agg["malformed"] / a, 2),
                "illegal_pct": round(100 * agg["illegal"] / a, 2),
                "dead_vote_pct": round(100 * agg["dead_votes"] / a, 2),
                "bad_pct": round(100 * (agg["malformed"] + agg["illegal"] + agg["timeout"]) / a, 2)}

    within = {"DeepInfra_fp8": host_quant("DeepInfra", "fp8"),
              "DeepInfra_fp4": host_quant("DeepInfra", "fp4")}

    out = {
        "season": "season-2",
        "n_games": n,
        "model": "google/gemma-4-31b-it",
        "wins": wins,
        "total_cost": round(total_cost, 4),
        "cost_per_game": round(total_cost / n, 4) if n else 0,
        "per_quant": perq,
        "quant_ratings": quant_ratings,
        "within_host": within,
        "routes": routes_out,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)

    # ---- console summary --------------------------------------------------
    print(f"Season 2: {n} games, ${total_cost:.2f} (${total_cost/n:.4f}/game)")
    print(f"wins: wolves {wins.get('werewolves',0)} / villagers {wins.get('villagers',0)}\n")
    print("AVERAGE PLAYER per precision (pooled re-score, 3x games each):")
    print(f"  {'quant':<6}{'overall':>8}{'wolf':>7}{'(rd)':>6}{'villager':>10}{'(rd)':>6}")
    for q in ("bf16", "fp8", "fp4"):
        r = quant_ratings.get(q)
        if r:
            print(f"  {q:<6}{r['overall']:>8}{r['wolf']['rating']:>7}{r['wolf']['rd']:>6}"
                  f"{r['villager']['rating']:>10}{r['villager']['rd']:>6}")
    print(f"\n{'quant':<6}{'actions':>8}{'malf%':>7}{'illegal%':>9}{'deadvote%':>10}{'bad%':>7}{'wolfWR':>8}{'vilWR':>7}")
    for q in ("bf16", "fp8", "fp4"):
        d = perq[q]
        print(f"{q:<6}{d['actions']:>8}{d['malformed_pct']:>7}{d['illegal_pct']:>9}"
              f"{d['dead_vote_pct']:>10}{d['bad_pct']:>7}{str(d['wolf_wr']):>8}{str(d['village_wr']):>7}")
    print("\nReplica NOISE FLOOR (spread across the 3 identical routes at each quant):")
    for q in ("bf16", "fp8", "fp4"):
        nf = perq[q]["noise_floor"]
        print(f"  {q}: illegal% {nf['illegal_pct_range']}  dead% {nf['dead_vote_pct_range']}  "
              f"overall-rating spread {nf['overall_rating_spread']}")
    print("\nWithin-host (DeepInfra only, zero provider confound):")
    for k, v in within.items():
        print(f"  {k:<16} bad% {v['bad_pct']:>5}  illegal% {v['illegal_pct']:>5}  dead% {v['dead_vote_pct']:>5}  (n={v['actions']})")
    print("\nPer-route detail:")
    print(f"  {'route':<20}{'acts':>5}{'bad%':>6}{'ill%':>6}{'dead%':>6}{'overall':>8}")
    for l in sorted(routes_out, key=lambda x: (QUANT_ORDER[parse_label(x)[1]], x)):
        r = routes_out[l]
        print(f"  {l.split('@')[1]:<20}{r['actions']:>5}{r['bad_pct']:>6}{r['illegal_pct']:>6}"
              f"{r['dead_vote_pct']:>6}{r['overall_rating']:>8}")
    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()
