"""Decision metrics: villager vote accuracy, survival, efficiency (Season 1).

PURE OFFLINE — reads the 108 stored records, makes no model/API calls. Writes
analysis/out/votes.json. Reuses loader.py, quant.py's wilson()/day_eliminations(),
and significance.py's two_prop_z()/tie_groups(), matching the house style of
REPORT.md: every rate carries a denominator, a Wilson 95% CI for small n, and a
two-proportion z-test (honest tie-bands) before any cross-model claim.

Metric 1 — villager vote accuracy: of the day-votes a model casts while on the
  village side, what share land on an actual werewolf. Denominator is accepted
  votes only (engine guarantees these hit a LIVING non-self target), so the
  state-tracking failures of §5 (dead-target/self/illegal) are excluded by
  construction — "clean != good" cuts both ways: detection skill is measured apart
  from hygiene. Split D1 (round 0) vs D2+; compare to a per-vote moving baseline
  (alive wolves / alive eligible targets).
Metric 2 — survival: share of seats alive at game end, split overall / wolf-side /
  village-side. Survival is team-driven (wolves are never night-killed and win by
  parity), so it is led on village-side and framed as a behavioural trait, not skill.
Metric 3 — efficiency: calls/game alongside output-tokens/call. Latency is absent
  from the schema (GameCost has calls but no wall-time) — reported as a data gap.
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scoring
from analysis.loader import load_games, seat_maps, short, WOLF_ROLE
from analysis.quant import wilson, day_eliminations
from analysis.significance import two_prop_z, tie_groups

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
os.makedirs(OUT, exist_ok=True)


def _death_rounds(game):
    """Map seat -> round it died, split by cause (night kill vs day elimination)."""
    night_kill, elim = {}, {}
    for e in game["events"]:
        if e["kind"] == "killed":
            night_kill[e["target_seat_id"]] = e["round_number"]
        elif e["kind"] == "eliminated":
            elim[e["target_seat_id"]] = e["round_number"]
    return night_kill, elim


def _alive_at_vote(all_seats, night_kill, elim, r):
    """Seats alive when the round-r day vote is cast: night kills of rounds <= r
    have happened; day eliminations of rounds < r have happened; the round-r
    elimination resolves only AFTER the vote, so its target is still alive."""
    alive = set()
    for s in all_seats:
        nk, el = night_kill.get(s), elim.get(s)
        dead = (nk is not None and nk <= r) or (el is not None and el < r)
        if not dead:
            alive.add(s)
    return alive


def _bucket(r):
    return "d1" if r == 0 else "d2plus"


def _band(records):
    """records: dict model -> (hits, n). Return tie-bands (best->worst) where a
    model joins the current band unless significantly worse than the band leader."""
    recs = sorted(((m, h, n) for m, (h, n) in records.items() if n > 0),
                  key=lambda t: -t[1] / t[2])
    return [[m for m, _, _ in g] for g in tie_groups(recs)]


def main():
    games = load_games()
    n_games = len(games)
    R = {"corpus_games": n_games}

    # ---------- METRIC 1 : villager vote accuracy ----------------------------
    # per model -> bucket -> [hits, n, baseline_sum]
    acc = defaultdict(lambda: {"d1": [0, 0, 0.0], "d2plus": [0, 0, 0.0]})
    team = {"d1": [0, 0, 0.0], "d2plus": [0, 0, 0.0]}  # pooled village votes
    # reconciliation counters over ALL action_type == "vote"
    rec = {"vote_actions": 0, "village_accepted": 0, "wolf_accepted": 0,
           "non_accepted": 0, "abstain_actions": 0}

    for g in games:
        sm, sr = seat_maps(g)
        wolves = {s for s in sm if sr.get(s) == WOLF_ROLE}
        all_seats = set(sm)
        night_kill, elim = _death_rounds(g)
        for a in g["actions"]:
            if a["action_type"] == "abstain":
                rec["abstain_actions"] += 1
                continue
            if a["action_type"] != "vote":
                continue
            rec["vote_actions"] += 1
            if a["outcome"] != "accepted":
                rec["non_accepted"] += 1
                continue
            voter, target = a["seat_id"], a["target_seat_id"]
            if sr.get(voter) == WOLF_ROLE:
                rec["wolf_accepted"] += 1
                continue
            # village-side accepted vote on a living non-self target (guaranteed)
            rec["village_accepted"] += 1
            b = _bucket(a["round_number"])
            hit = 1 if sr.get(target) == WOLF_ROLE else 0
            alive = _alive_at_vote(all_seats, night_kill, elim, a["round_number"])
            elig = len(alive) - 1  # may not vote self
            exp = (len(wolves & alive) / elig) if elig > 0 else 0.0
            m = short(sm[voter])
            acc[m][b][0] += hit; acc[m][b][1] += 1; acc[m][b][2] += exp
            team[b][0] += hit; team[b][1] += 1; team[b][2] += exp

    def pack(cell):
        h, n, esum = cell
        p, lo, hi = wilson(h, n)
        base = esum / n if n else None
        return {"hits": h, "n": n, "accuracy": round(p, 3) if n else None,
                "ci95": [round(lo, 3), round(hi, 3)] if n else None,
                "baseline": round(base, 3) if base is not None else None,
                "above_chance": round(p - base, 3) if n else None}

    vote_models = {}
    for m, d in acc.items():
        allh = d["d1"][0] + d["d2plus"][0]
        alln = d["d1"][1] + d["d2plus"][1]
        allb = d["d1"][2] + d["d2plus"][2]
        vote_models[m] = {"d1": pack(d["d1"]), "d2plus": pack(d["d2plus"]),
                          "overall": pack([allh, alln, allb])}

    # significance tie-bands (D2+ = truer detection signal; and overall)
    d2_band = _band({m: (d["d2plus"][0], d["d2plus"][1]) for m, d in acc.items()})
    overall_band = _band({m: (d["d1"][0] + d["d2plus"][0], d["d1"][1] + d["d2plus"][1])
                          for m, d in acc.items()})
    # count significant pairs in D2+
    d2_recs = [(m, d["d2plus"][0], d["d2plus"][1]) for m, d in acc.items() if d["d2plus"][1] > 0]
    sig = sum(1 for i in range(len(d2_recs)) for j in range(i + 1, len(d2_recs))
              if two_prop_z(d2_recs[i][1], d2_recs[i][2], d2_recs[j][1], d2_recs[j][2]) < 0.05)
    n_pairs = len(d2_recs) * (len(d2_recs) - 1) // 2

    # team-level + cross-check vs quant.py team_mislynch_rate (1 - lynch accuracy)
    elim_hit = {"d1": [0, 0], "d2plus": [0, 0]}  # eliminations hitting a wolf
    for g in games:
        for rnum, seat, role, was_wolf in day_eliminations(g):
            b = _bucket(rnum)
            elim_hit[b][1] += 1
            if was_wolf:
                elim_hit[b][0] += 1

    def teamrate(cell):
        h, n, esum = cell
        return {"accuracy": round(h / n, 3) if n else None, "hits": h, "n": n,
                "baseline": round(esum / n, 3) if n else None}

    R["metric1_vote_accuracy"] = {
        "per_model": vote_models,
        "team": {"d1": teamrate(team["d1"]), "d2plus": teamrate(team["d2plus"])},
        "d2plus_tie_bands": d2_band,
        "overall_tie_bands": overall_band,
        "d2plus_sig_pairs": sig, "d2plus_n_pairs": n_pairs,
        "crosscheck_vs_quant_mislynch": {
            "note": "team vote accuracy (per-vote) vs elimination accuracy = 1 - team_mislynch_rate (per-lynch). Different units; should agree in direction/ballpark.",
            "d1_vote_accuracy": round(team["d1"][0] / team["d1"][1], 3) if team["d1"][1] else None,
            "d1_elim_accuracy_1_minus_mislynch": round(elim_hit["d1"][0] / elim_hit["d1"][1], 3) if elim_hit["d1"][1] else None,
            "d2plus_vote_accuracy": round(team["d2plus"][0] / team["d2plus"][1], 3) if team["d2plus"][1] else None,
            "d2plus_elim_accuracy_1_minus_mislynch": round(elim_hit["d2plus"][0] / elim_hit["d2plus"][1], 3) if elim_hit["d2plus"][1] else None,
        },
        "reconciliation": rec,
    }

    # ---------- METRIC 2 : survival ------------------------------------------
    surv = defaultdict(lambda: {"overall": [0, 0], "wolf": [0, 0], "village": [0, 0]})
    tot_surv_seats = tot_seats = 0
    for g in games:
        sm, sr = seat_maps(g)
        survivors = set(g["result"]["surviving_seat_ids"])
        for s, model in sm.items():
            m = short(model)
            alive = 1 if s in survivors else 0
            side = "wolf" if sr.get(s) == WOLF_ROLE else "village"
            surv[m]["overall"][0] += alive; surv[m]["overall"][1] += 1
            surv[m][side][0] += alive; surv[m][side][1] += 1
            tot_surv_seats += alive; tot_seats += 1

    def packs(cell):
        k, n = cell
        p, lo, hi = wilson(k, n)
        return {"survived": k, "seats": n, "rate": round(p, 3) if n else None,
                "ci95": [round(lo, 3), round(hi, 3)] if n else None}

    surv_models = {m: {"overall": packs(d["overall"]), "wolf": packs(d["wolf"]),
                       "village": packs(d["village"])} for m, d in surv.items()}
    village_band = _band({m: (d["village"][0], d["village"][1]) for m, d in surv.items()})
    v_recs = [(m, d["village"][0], d["village"][1]) for m, d in surv.items()]
    v_sig = sum(1 for i in range(len(v_recs)) for j in range(i + 1, len(v_recs))
                if two_prop_z(v_recs[i][1], v_recs[i][2], v_recs[j][1], v_recs[j][2]) < 0.05)
    R["metric2_survival"] = {
        "per_model": surv_models,
        "village_tie_bands": village_band,
        "village_sig_pairs": v_sig, "village_n_pairs": len(v_recs) * (len(v_recs) - 1) // 2,
        "corpus_survival_rate": round(tot_surv_seats / tot_seats, 3),
        "corpus_surviving_seats": tot_surv_seats, "corpus_total_seats": tot_seats,
    }

    # ---------- METRIC 3 : efficiency ----------------------------------------
    # score_games() returns a dict ordered by a set iteration (hash-randomised),
    # so sort for a deterministic, idempotent write.
    scored = scoring.score_games(games)
    eff = {}
    for model, s in sorted(scored.items()):
        st = s["stats"]
        gp, calls = st["games"], st["calls"]
        eff[short(model)] = {
            "games": gp, "calls": calls,
            "calls_per_game": round(calls / gp, 2) if gp else None,
            "out_tok_per_call": round(st["output_tokens"] / calls) if calls else None,
            "in_tok_per_call": round(st["input_tokens"] / calls) if calls else None,
            "cost_per_game_usd": round(st["cost"] / gp, 5) if gp else None,
        }
    R["metric3_efficiency"] = {
        "per_model": eff,
        "latency_data_gap": ("GameCost stores calls but NO wall-time; Action has no "
                             "timing field. No per-call latency exists in the records. "
                             "Not estimated. v3 fix: instrument per-call latency in the "
                             "adapter so avg-latency / calls-per-game columns become available."),
        "calls_confound": ("calls/game is driven by survival (longer-living seats take "
                           "more turns) and role (wolves/seer/healer add night calls), "
                           "not by model efficiency alone — read alongside Metric 2."),
    }

    with open(os.path.join(OUT, "votes.json"), "w", encoding="utf-8") as f:
        json.dump(R, f, indent=2)
    print("Wrote", os.path.join(OUT, "votes.json"))
    _print_report(R)
    _validate(R)
    return R


def _print_report(R):
    m1 = R["metric1_vote_accuracy"]
    print("\n=== METRIC 1: villager vote accuracy (D2+ is the truer detection signal) ===")
    print(f"{'model':14s} {'D1 acc[95%CI]':>22s} {'base':>5s} {'D2+ acc[95%CI]':>22s} {'base':>5s} {'D2+ Δchance':>11s}")
    for m, d in sorted(m1["per_model"].items(), key=lambda kv: -(kv[1]['d2plus']['accuracy'] or 0)):
        d1, d2 = d["d1"], d["d2plus"]
        d1s = f"{d1['accuracy']:.2f}[{d1['ci95'][0]:.2f},{d1['ci95'][1]:.2f}]" if d1['n'] else "--"
        d2s = f"{d2['accuracy']:.2f}[{d2['ci95'][0]:.2f},{d2['ci95'][1]:.2f}]" if d2['n'] else "--"
        print(f"{m:14s} {d1s:>22s} {str(d1['baseline']):>5s} {d2s:>22s} {str(d2['baseline']):>5s} {str(d2['above_chance']):>11s}")
    print(f"team D1 vote acc={m1['team']['d1']['accuracy']} (base {m1['team']['d1']['baseline']}) | "
          f"D2+ vote acc={m1['team']['d2plus']['accuracy']} (base {m1['team']['d2plus']['baseline']})")
    print(f"D2+ tie-bands: {m1['d2plus_tie_bands']}")
    print(f"D2+ significant pairs: {m1['d2plus_sig_pairs']}/{m1['d2plus_n_pairs']}")

    m2 = R["metric2_survival"]
    print("\n=== METRIC 2: survival (lead on village-side; survival != skill) ===")
    print(f"{'model':14s} {'overall':>8s} {'wolf':>8s} {'village[95%CI]':>22s}")
    for m, d in sorted(m2["per_model"].items(), key=lambda kv: -(kv[1]['village']['rate'] or 0)):
        v = d["village"]
        vs = f"{v['rate']:.2f}[{v['ci95'][0]:.2f},{v['ci95'][1]:.2f}]"
        print(f"{m:14s} {d['overall']['rate']:8.2f} {d['wolf']['rate']:8.2f} {vs:>22s}")
    print(f"village tie-bands: {m2['village_tie_bands']}")
    print(f"village significant pairs: {m2['village_sig_pairs']}/{m2['village_n_pairs']}")

    m3 = R["metric3_efficiency"]
    print("\n=== METRIC 3: efficiency (calls/game confounded by survival + role) ===")
    print(f"{'model':14s} {'calls/game':>10s} {'out/call':>9s} {'in/call':>8s} {'$/game':>8s}")
    for m, d in sorted(m3["per_model"].items(), key=lambda kv: -kv[1]['calls_per_game']):
        print(f"{m:14s} {d['calls_per_game']:10.2f} {d['out_tok_per_call']:9d} {d['in_tok_per_call']:8d} {d['cost_per_game_usd']:8.5f}")
    print("LATENCY:", m3["latency_data_gap"])


def _validate(R):
    print("\n=== VALIDATION ===")
    rec = R["metric1_vote_accuracy"]["reconciliation"]
    lhs = rec["village_accepted"] + rec["wolf_accepted"] + rec["non_accepted"]
    print(f"[1] vote reconciliation: village_accepted({rec['village_accepted']}) + "
          f"wolf_accepted({rec['wolf_accepted']}) + non_accepted({rec['non_accepted']}) "
          f"= {lhs}  vs  total vote actions = {rec['vote_actions']}  -> "
          f"{'OK' if lhs == rec['vote_actions'] else 'MISMATCH'}  (abstains, separate type: {rec['abstain_actions']})")
    cc = R["metric1_vote_accuracy"]["crosscheck_vs_quant_mislynch"]
    print(f"[2] team vote-acc vs elim-acc(=1-mislynch): "
          f"D1 {cc['d1_vote_accuracy']} vs {cc['d1_elim_accuracy_1_minus_mislynch']} | "
          f"D2+ {cc['d2plus_vote_accuracy']} vs {cc['d2plus_elim_accuracy_1_minus_mislynch']} "
          f"(per-vote vs per-lynch; expect same ballpark)")
    m2 = R["metric2_survival"]
    print(f"[3] corpus survival: {m2['corpus_surviving_seats']}/{m2['corpus_total_seats']} "
          f"= {m2['corpus_survival_rate']} (avg seats alive at end per game = "
          f"{round(m2['corpus_surviving_seats'] / R['corpus_games'], 2)})")


if __name__ == "__main__":
    main()
