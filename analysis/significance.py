"""Statistical significance + honest tie-grouping for Season 1 (PLAN item 1).

Glicko RD already gives uncertainty bands. This adds a frequentist cross-check:
pairwise two-proportion z-tests on win rates, and tie-groups built so that
models not separated at p<0.05 sit in the same band. Also quantifies whether
positional/seating noise could plausibly drive the ladder (item 10).
"""
from __future__ import annotations
import json, math, os
from itertools import combinations
from collections import defaultdict
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scoring
from analysis.loader import load_games, seat_maps, short, WOLF_ROLE

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")


def two_prop_z(k1, n1, k2, n2):
    """Two-sided two-proportion z-test p-value."""
    if n1 == 0 or n2 == 0:
        return 1.0
    p1, p2 = k1 / n1, k2 / n2
    p = (k1 + k2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return 1.0
    z = (p1 - p2) / se
    return 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))


def tie_groups(records):
    """records: list of (label, wins, games) sorted best->worst. A model joins the
    current band unless it is significantly worse (p<0.05) than the band LEADER
    (the top model of that band); a significant gap opens a new band. This yields
    honest "within noise" bands rather than a forced 1-2-3 ranking."""
    groups = []
    for label, w, n in records:
        if not groups:
            groups.append([(label, w, n)]); continue
        leader = groups[-1][0]  # top of current band
        if two_prop_z(w, n, leader[1], leader[2]) < 0.05:
            groups.append([(label, w, n)])      # sig worse than band leader -> new band
        else:
            groups[-1].append((label, w, n))
    return groups


def main():
    games = load_games()
    scored = scoring.score_games(games)
    rows = []
    for m, s in scored.items():
        rows.append((short(m), s))

    out = {}
    for ladder, key in [("wolf", "wolf"), ("villager", "villager")]:
        recs = sorted([(short(m), s[key]["wins"], s[key]["games"]) for m, s in scored.items()],
                      key=lambda r: -r[1] / r[2])
        # pairwise p matrix
        pm = {}
        for (a, wa, na), (b, wb, nb) in combinations(recs, 2):
            pm[f"{a} vs {b}"] = round(two_prop_z(wa, na, wb, nb), 3)
        groups = tie_groups(recs)
        out[ladder] = {
            "ranking": [{"model": a, "wins": w, "games": n, "wr": round(w / n, 3)} for a, w, n in recs],
            "tie_groups": [[a for a, _, _ in g] for g in groups],
            "n_sig_pairs": sum(1 for v in pm.values() if v < 0.05),
            "n_pairs": len(pm),
            "sig_pairs": {k: v for k, v in pm.items() if v < 0.05},
        }

    # overall: combine wolf+villager wins / games (whole-player win rate)
    recs = sorted([(short(m), s["stats"]["wolf_wins"] + s["stats"]["village_wins"],
                    s["stats"]["games"]) for m, s in scored.items()], key=lambda r: -r[1] / r[2])
    out["overall_winrate"] = {
        "ranking": [{"model": a, "wins": w, "games": n, "wr": round(w / n, 3)} for a, w, n in recs],
        "tie_groups": [[a for a, _, _ in g] for g in tie_groups(recs)],
    }

    # Glicko 95% band overlap (±1.96 RD) tie grouping for wolf & villager
    for ladder, key in [("wolf", "wolf"), ("villager", "villager")]:
        bands = sorted([(short(m), s[key]["rating"], s[key]["rd"]) for m, s in scored.items()],
                       key=lambda r: -r[1])
        out[f"{ladder}_glicko_bands"] = [
            {"model": a, "rating": round(r, 0), "rd": round(rd, 0),
             "lo95": round(r - 1.96 * rd, 0), "hi95": round(r + 1.96 * rd, 0)} for a, r, rd in bands]

    with open(os.path.join(OUT, "significance.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print("Wrote significance.json\n")
    for ladder in ("wolf", "villager", "overall_winrate"):
        print(f"=== {ladder} tie-groups (not separated at p<0.05) ===")
        for i, g in enumerate(out[ladder]["tie_groups"], 1):
            print(f"  Band {i}: {', '.join(g)}")
        if ladder != "overall_winrate":
            print(f"  significant pairs: {out[ladder]['n_sig_pairs']}/{out[ladder]['n_pairs']}")
        print()
    print("=== WOLF Glicko 95% bands ===")
    for b in out["wolf_glicko_bands"]:
        print(f"  {b['model']:14s} {b['rating']:.0f}  [{b['lo95']:.0f}, {b['hi95']:.0f}]")
    return out


if __name__ == "__main__":
    main()
