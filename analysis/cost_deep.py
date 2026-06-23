"""Deep cost / efficiency analysis (Season 1).

Recovers each model's ACTUAL input/output per-token price by least-squares over
its per-game (input_tokens, output_tokens, total_cost) triples — the stored
records keep only a blended total, but price is constant per model so a 2-unknown
fit over ~100 games recovers it near-exactly. Then decomposes spend into
price-vs-verbosity, builds the cost/rating Pareto frontier, and reports cost-per-win.
Pure offline.
"""
from __future__ import annotations
import json, os
from collections import defaultdict
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scoring
from analysis.loader import load_games, short

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")


def fit_prices(rows):
    """Least squares for total = p_in*in + p_out*out (no intercept) over a model's
    games. Returns (p_in, p_out, r2). Solves the 2x2 normal equations directly."""
    Sii = Sio = Soo = Sit = Sot = Stt = 0.0
    n = 0
    for i, o, t in rows:
        if t <= 0:
            continue
        Sii += i * i; Sio += i * o; Soo += o * o
        Sit += i * t; Sot += o * t; Stt += t * t; n += 1
    det = Sii * Soo - Sio * Sio
    if abs(det) < 1e-9 or n < 3:
        return None, None, None
    p_in = (Sit * Soo - Sot * Sio) / det
    p_out = (Sot * Sii - Sit * Sio) / det
    # R^2
    ss_res = sum((t - (p_in * i + p_out * o)) ** 2 for i, o, t in rows if t > 0)
    mean_t = sum(t for _, _, t in rows if t > 0) / n
    ss_tot = sum((t - mean_t) ** 2 for _, _, t in rows if t > 0)
    r2 = 1 - ss_res / ss_tot if ss_tot else 1.0
    return p_in, p_out, r2


def main():
    games = load_games()
    scored = scoring.score_games(games)
    per_game = defaultdict(list)  # model -> [(in, out, total), ...]
    for g in games:
        for m, c in (g.get("model_costs") or {}).items():
            per_game[short(m)].append((c.get("input_tokens", 0), c.get("output_tokens", 0),
                                       c.get("total_cost", 0.0)))

    out = {"total_season_usd": round(sum(g.get("cost", {}).get("total_cost", 0) for g in games), 4),
           "per_model": {}}
    for m, st in ((short(k), v["stats"]) for k, v in scored.items()):
        rows = per_game[m]
        p_in, p_out, r2 = fit_prices(rows)
        calls = st["calls"]; it = st["input_tokens"]; ot = st["output_tokens"]
        c = st["cost"]; gp = st["games"]; wins = st["wolf_wins"] + st["village_wins"]
        out["per_model"][m] = {
            "total_usd": round(c, 4), "games": gp,
            "cost_per_game": round(c / gp, 5) if gp else None,
            "cost_per_win": round(c / wins, 5) if wins else None,
            "calls": calls,
            "in_tok_per_call": round(it / calls) if calls else None,
            "out_tok_per_call": round(ot / calls) if calls else None,   # verbosity
            "price_in_per_M": round(p_in * 1e6, 4) if p_in is not None else None,
            "price_out_per_M": round(p_out * 1e6, 4) if p_out is not None else None,
            "blended_per_M": round(c / (it + ot) * 1e6, 4) if (it + ot) else None,
            "price_fit_r2": round(r2, 5) if r2 is not None else None,
            "overall_rating": round(scored_rating(scored, m), 0),
            "value_per_usd": round((scored_rating(scored, m) - 1500) / c, 1) if c > 0 else None,
        }

    # Pareto frontier: a model is dominated if another is both cheaper AND higher-rated.
    pm = out["per_model"]
    for m, d in pm.items():
        dominated_by = [m2 for m2, d2 in pm.items()
                        if m2 != m and d2["total_usd"] <= d["total_usd"]
                        and d2["overall_rating"] >= d["overall_rating"]
                        and (d2["total_usd"] < d["total_usd"] or d2["overall_rating"] > d["overall_rating"])]
        d["pareto_optimal"] = len(dominated_by) == 0
        d["dominated_by"] = dominated_by

    with open(os.path.join(OUT, "cost_deep.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print("Wrote cost_deep.json | season total $%.2f\n" % out["total_season_usd"])
    print(f"{'model':14s} {'$tot':>6s} {'$/game':>7s} {'$/win':>6s} {'in$/M':>6s} {'out$/M':>7s} {'out/call':>8s} {'rating':>6s} {'val/$':>6s} {'fitR2':>6s} {'Pareto':>6s}")
    for m, d in sorted(pm.items(), key=lambda kv: -kv[1]["total_usd"]):
        print(f"{m:14s} {d['total_usd']:6.3f} {d['cost_per_game']:7.5f} {d['cost_per_win']:6.4f} "
              f"{d['price_in_per_M']:6.3f} {d['price_out_per_M']:7.3f} {d['out_tok_per_call']:8d} "
              f"{d['overall_rating']:6.0f} {str(d['value_per_usd']):>6s} {d['price_fit_r2']:6.3f} "
              f"{'YES' if d['pareto_optimal'] else 'no':>6s}")
    return out


def scored_rating(scored, m):
    for k, v in scored.items():
        if short(k) == m:
            return v["overall"]["rating"]
    return 1500.0


if __name__ == "__main__":
    main()
