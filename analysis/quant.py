"""Season 1 quantitative analysis (PLAN items 1-6, 8-10).

Pure offline computation over the 108 stored records. Reuses scoring.py for the
role-conditional Glicko-2 ratings. Writes analysis/out/quant.json and prints a
readable report. No model/API calls.
"""
from __future__ import annotations

import json
import math
import os
import re
from collections import defaultdict, Counter

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import scoring
from analysis.loader import load_games, seat_maps, short, is_wolf, WOLF_ROLE, VILLAGE_ROLES, SHORT

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
os.makedirs(OUT, exist_ok=True)


def wilson(k: int, n: int, z: float = 1.96):
    """Wilson score 95% CI for a proportion — honest for small n."""
    if n == 0:
        return (0.0, 0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (p, max(0.0, centre - half), min(1.0, centre + half))


def first_day_elim(rec):
    """Return (eliminated_seat, was_wolf) for the round-0 day vote, or None."""
    sm, sr = seat_maps(rec)
    for e in rec["events"]:
        if e["kind"] == "eliminated" and e["round_number"] == 0:
            seat = e["target_seat_id"]
            return seat, sr.get(seat) == WOLF_ROLE
    return None


def day_eliminations(rec):
    """List of (round_number, seat, role, was_wolf) for each day elimination."""
    sm, sr = seat_maps(rec)
    out = []
    for e in rec["events"]:
        if e["kind"] == "eliminated":
            seat = e["target_seat_id"]
            out.append((e["round_number"], seat, sr.get(seat), sr.get(seat) == WOLF_ROLE))
    return out


def main():
    games = load_games()
    R = {}  # the big results dict

    # ---- 0. corpus + scoring ------------------------------------------------
    R["corpus"] = {
        "games": len(games),
        "void": sum(1 for g in games if g.get("void")),
        "prompt_version": sorted({g.get("prompt_version") for g in games}),
        "winners": dict(Counter(g["result"]["winner"] for g in games)),
        "rounds_dist": dict(sorted(Counter(g["result"]["rounds_played"] for g in games).items())),
    }
    scored = scoring.score_games(games)
    R["ratings"] = {short(m): s for m, s in scored.items()}

    # ---- 1b. provisional (first 31) vs final (108) shift --------------------
    first31 = games[:31]
    prov = scoring.score_games(first31)
    def order(d, key):
        return [short(m) for m, _ in sorted(d.items(), key=lambda kv: -kv[1][key]["rating"])]
    R["provisional_shift"] = {
        "n_games_prov": len(first31),
        "overall_order_prov": order(prov, "overall"),
        "overall_order_final": order(scored, "overall"),
        "prov_ratings": {short(m): round(s["overall"]["rating"], 0) for m, s in prov.items()},
        "final_ratings": {short(m): round(s["overall"]["rating"], 0) for m, s in scored.items()},
    }

    # ---- 2. role asymmetry: wolf (liar) vs villager (detector) win rates ----
    asym = {}
    for m, s in scored.items():
        wg, ww = s["stats"]["wolf_games"], s["stats"]["wolf_wins"]
        vg, vw = s["stats"]["village_games"], s["stats"]["village_wins"]
        wp, wlo, whi = wilson(ww, wg)
        vp, vlo, vhi = wilson(vw, vg)
        asym[short(m)] = {
            "wolf_games": wg, "wolf_wins": ww, "wolf_wr": round(wp, 3),
            "wolf_wr_ci": [round(wlo, 3), round(whi, 3)],
            "wolf_rating": round(s["wolf"]["rating"], 0), "wolf_rd": round(s["wolf"]["rd"], 0),
            "vil_games": vg, "vil_wins": vw, "vil_wr": round(vp, 3),
            "vil_wr_ci": [round(vlo, 3), round(vhi, 3)],
            "vil_rating": round(s["villager"]["rating"], 0), "vil_rd": round(s["villager"]["rd"], 0),
            "rating_gap_wolf_minus_vil": round(s["wolf"]["rating"] - s["villager"]["rating"], 0),
        }
    R["role_asymmetry"] = asym

    # ---- 3. win dynamics ----------------------------------------------------
    # how games end: werewolves win == reached parity; villagers win == all wolves dead
    end = {"parity_wolves_win": 0, "all_wolves_dead": 0}
    for g in games:
        if g["result"]["winner"] == "werewolves":
            end["parity_wolves_win"] += 1
        else:
            end["all_wolves_dead"] += 1
    # Day-1 lynch accuracy + does catching a wolf D1 predict village win
    d1_wolf, d1_total, d1_nokill = 0, 0, 0
    win_given_d1wolf = [0, 0]   # [village_wins, total] when D1 caught a wolf
    win_given_d1miss = [0, 0]
    for g in games:
        fe = first_day_elim(g)
        if fe is None:
            d1_nokill += 1
            continue
        d1_total += 1
        vil_win = g["result"]["winner"] == "villagers"
        if fe[1]:
            d1_wolf += 1
            win_given_d1wolf[0] += vil_win; win_given_d1wolf[1] += 1
        else:
            win_given_d1miss[0] += vil_win; win_given_d1miss[1] += 1
    R["win_dynamics"] = {
        "winners": R["corpus"]["winners"],
        "village_wr": round(R["corpus"]["winners"].get("villagers", 0) / len(games), 3),
        "wolf_wr": round(R["corpus"]["winners"].get("werewolves", 0) / len(games), 3),
        "rounds_dist": R["corpus"]["rounds_dist"],
        "mean_rounds": round(sum(g["result"]["rounds_played"] for g in games) / len(games), 2),
        "how_games_end": end,
        "d1_lynch": {
            "games_with_d1_elim": d1_total, "games_no_d1_elim": d1_nokill,
            "d1_hit_wolf": d1_wolf, "d1_accuracy": round(d1_wolf / d1_total, 3) if d1_total else None,
            "d1_random_baseline": round(2 / 9, 3),  # 2 wolves of 9 seats (pre-night-death ~2/8)
        },
        "d1_catch_predicts_win": {
            "vil_wr_when_d1_caught_wolf": [win_given_d1wolf[0], win_given_d1wolf[1],
                round(win_given_d1wolf[0] / win_given_d1wolf[1], 3) if win_given_d1wolf[1] else None],
            "vil_wr_when_d1_missed": [win_given_d1miss[0], win_given_d1miss[1],
                round(win_given_d1miss[0] / win_given_d1miss[1], 3) if win_given_d1miss[1] else None],
        },
    }

    # ---- 4. wolf-pair synergy ----------------------------------------------
    pair_games = defaultdict(lambda: [0, 0])  # frozenset(pair) -> [wins, games]
    model_partner = defaultdict(lambda: defaultdict(lambda: [0, 0]))  # m -> partner -> [wins,games]
    for g in games:
        sm, sr = seat_maps(g)
        wolves = [sm[s] for s in sm if sr.get(s) == WOLF_ROLE]
        if len(wolves) != 2:
            continue
        win = 1 if g["result"]["winner"] == "werewolves" else 0
        key = frozenset(short(w) for w in wolves)
        pair_games[key][0] += win; pair_games[key][1] += 1
        a, b = short(wolves[0]), short(wolves[1])
        model_partner[a][b][0] += win; model_partner[a][b][1] += 1
        if a != b:
            model_partner[b][a][0] += win; model_partner[b][a][1] += 1
    R["wolf_pairs"] = {
        " + ".join(sorted(k)): {"wins": v[0], "games": v[1], "wr": round(v[0] / v[1], 3)}
        for k, v in sorted(pair_games.items(), key=lambda kv: -kv[1][0] / kv[1][1])
    }
    R["partner_dependence"] = {
        m: {p: {"wins": v[0], "games": v[1], "wr": round(v[0] / v[1], 3)}
            for p, v in sorted(partners.items(), key=lambda kv: -kv[1][0] / max(1, kv[1][1]))}
        for m, partners in sorted(model_partner.items())
    }

    # ---- 5. behavioral/quality signals (rates per model) --------------------
    beh = {}
    for m, s in scored.items():
        st = s["stats"]
        acts = st["actions"]
        beh[short(m)] = {
            "actions": acts,
            "refused": st["refused"], "refused_rate": round(st["refused"] / acts, 4) if acts else 0,
            "malformed": st["malformed"], "malformed_rate": round(st["malformed"] / acts, 4) if acts else 0,
            "illegal": st["illegal"], "illegal_rate": round(st["illegal"] / acts, 4) if acts else 0,
            "timeout": st["timeout"], "timeout_rate": round(st["timeout"] / acts, 4) if acts else 0,
            "dead_votes": st["dead_votes"], "dead_vote_rate": round(st["dead_votes"] / acts, 4) if acts else 0,
        }
    R["behavioral"] = beh

    # ---- 6. manipulation metrics -------------------------------------------
    # manipulation success as wolf: a day elimination lands on a non-wolf while
    # the model is an alive wolf. credit per alive wolf model. split D1 (round0) vs D2+.
    manip = defaultdict(lambda: {"d1_mislynch": 0, "d1_elim": 0, "d2_mislynch": 0, "d2_elim": 0})
    # team-level: of all day eliminations, what share hit a villager?
    team_elim = {"d1": [0, 0], "d2plus": [0, 0]}  # [mislynch(non-wolf), total]
    for g in games:
        sm, sr = seat_maps(g)
        wolves = {s for s in sm if sr.get(s) == WOLF_ROLE}
        # track alive wolves over rounds: a wolf is alive at a given day vote if not
        # killed/eliminated before that round. reconstruct deaths timeline.
        dead_before_round = defaultdict(set)  # round -> set of seats dead before that day vote
        deaths = []  # (round, seat)
        for e in g["events"]:
            if e["kind"] in ("killed", "eliminated"):
                deaths.append((e["round_number"], e["kind"], e["target_seat_id"]))
        for rnum, seat, role, was_wolf in day_eliminations(g):
            # which wolves alive at this day vote? wolves not eliminated/killed in a strictly earlier resolution
            dead = set()
            for dr, dk, dseat in deaths:
                # night kill round r happens before day vote round r; elimination round r is this/earlier
                if dr < rnum or (dr == rnum and dk == "killed"):
                    dead.add(dseat)
            alive_wolves = wolves - dead
            bucket = "d1" if rnum == 0 else "d2plus"
            team_elim[bucket][1] += 1
            if not was_wolf:
                team_elim[bucket][0] += 1
            for w in alive_wolves:
                m = short(sm[w])
                if rnum == 0:
                    manip[m]["d1_elim"] += 1
                    if not was_wolf: manip[m]["d1_mislynch"] += 1
                else:
                    manip[m]["d2_elim"] += 1
                    if not was_wolf: manip[m]["d2_mislynch"] += 1
    R["manipulation"] = {
        "team_mislynch_rate_d1": round(team_elim["d1"][0] / team_elim["d1"][1], 3) if team_elim["d1"][1] else None,
        "team_mislynch_rate_d2plus": round(team_elim["d2plus"][0] / team_elim["d2plus"][1], 3) if team_elim["d2plus"][1] else None,
        "team_elim_counts": team_elim,
        "per_wolf_model": {
            m: {
                "d1_mislynch": v["d1_mislynch"], "d1_elim": v["d1_elim"],
                "d1_rate": round(v["d1_mislynch"] / v["d1_elim"], 3) if v["d1_elim"] else None,
                "d2_mislynch": v["d2_mislynch"], "d2_elim": v["d2_elim"],
                "d2_rate": round(v["d2_mislynch"] / v["d2_elim"], 3) if v["d2_elim"] else None,
            } for m, v in sorted(manip.items())
        },
    }

    # auto-sabotage: village eliminates its own power role (seer/healer)
    sabotage = {"games_with_powerrole_lynched": 0, "seer_lynched": 0, "healer_lynched": 0}
    sabotage_voters = Counter()  # who voted to lynch a power role
    for g in games:
        sm, sr = seat_maps(g)
        powerrole_lynched = False
        for rnum, seat, role, was_wolf in day_eliminations(g):
            if role in ("seer", "healer"):
                powerrole_lynched = True
                sabotage[f"{role}_lynched"] += 1
                # who voted for this seat that round?
                for e in g["events"]:
                    if e["kind"] == "vote" and e["round_number"] == rnum and e["target_seat_id"] == seat:
                        voter_role = sr.get(e["seat_id"])
                        if voter_role != WOLF_ROLE:  # villager-side voter sabotaging
                            sabotage_voters[short(sm[e["seat_id"]])] += 1
        if powerrole_lynched:
            sabotage["games_with_powerrole_lynched"] += 1
    R["auto_sabotage"] = {
        **sabotage,
        "rate_games": round(sabotage["games_with_powerrole_lynched"] / len(games), 3),
        "villager_votes_to_lynch_powerrole": dict(sabotage_voters.most_common()),
    }

    # vote-swing / persuasion via lean_target before/after messages
    # advocacy success: when a model states a lean_target during a day, did that
    # target get eliminated that day? lean stability: same-day consecutive leans.
    advocacy = defaultdict(lambda: {"advocacy_turns": 0, "hits": 0})
    lean_fill = {"speak_total": 0, "speak_with_lean": 0}
    for g in games:
        sm, sr = seat_maps(g)
        elim_by_round = {rnum: seat for rnum, seat, _, _ in day_eliminations(g)}
        for a in g["actions"]:
            if a["action_type"] == "speak":
                lean_fill["speak_total"] += 1
                lt = a.get("lean_target_seat_id")
                if lt is not None:
                    lean_fill["speak_with_lean"] += 1
                    m = short(sm.get(a["seat_id"]))
                    advocacy[m]["advocacy_turns"] += 1
                    if elim_by_round.get(a["round_number"]) == lt:
                        advocacy[m]["hits"] += 1
    R["persuasion"] = {
        "lean_fill": lean_fill,
        "advocacy_success": {
            m: {"turns": v["advocacy_turns"], "hits": v["hits"],
                "hit_rate": round(v["hits"] / v["advocacy_turns"], 3) if v["advocacy_turns"] else None}
            for m, v in sorted(advocacy.items(), key=lambda kv: -(kv[1]["hits"] / max(1, kv[1]["advocacy_turns"])))
        },
    }

    # ---- 8. power roles -----------------------------------------------------
    # seer: investigation usefulness (hit a wolf), survival, claims believed
    # healer: save rate (protect target == wolf kill target)
    seer_stat = defaultdict(lambda: {"games": 0, "investigations": 0, "wolf_finds": 0, "survived_to_end": 0, "vil_wins": 0})
    healer_stat = defaultdict(lambda: {"games": 0, "protects": 0, "saves": 0, "self_protect": 0, "vil_wins": 0})
    seer_claim_re = re.compile(r"\b(i am|i'm|as)\s+(the\s+)?seer\b|i investigat|my investigation|i checked|i am your seer", re.I)
    healer_claim_re = re.compile(r"\b(i am|i'm|as)\s+(the\s+)?(healer|doctor)\b|i protected|i healed|i saved", re.I)
    seer_claims = {"games_with_seer": 0, "seer_claimed": 0}
    for g in games:
        sm, sr = seat_maps(g)
        surviving = set(g["result"]["surviving_seat_ids"])
        vil_win = g["result"]["winner"] == "villagers"
        seer_seat = next((s for s in sm if sr.get(s) == "seer"), None)
        healer_seat = next((s for s in sm if sr.get(s) == "healer"), None)
        # night kill targets per round + protect targets per round
        kills = {}; protects = {}
        for a in g["actions"]:
            if a["action_type"] == "kill" and a["outcome"] == "accepted":
                kills[a["round_number"]] = a["target_seat_id"]
            if a["action_type"] == "protect" and a["outcome"] == "accepted":
                protects[a["round_number"]] = a["target_seat_id"]
        if seer_seat is not None:
            m = short(sm[seer_seat]); ss = seer_stat[m]
            ss["games"] += 1
            if seer_seat in surviving: ss["survived_to_end"] += 1
            if vil_win: ss["vil_wins"] += 1
            for a in g["actions"]:
                if a["seat_id"] == seer_seat and a["action_type"] == "investigate" and a["outcome"] == "accepted":
                    ss["investigations"] += 1
                    if sr.get(a["target_seat_id"]) == WOLF_ROLE:
                        ss["wolf_finds"] += 1
            # claim detection
            seer_claims["games_with_seer"] += 1
            claimed = any(a["seat_id"] == seer_seat and a.get("message") and seer_claim_re.search(a["message"])
                          for a in g["actions"])
            if claimed: seer_claims["seer_claimed"] += 1
        if healer_seat is not None:
            m = short(sm[healer_seat]); hs = healer_stat[m]
            hs["games"] += 1
            if vil_win: hs["vil_wins"] += 1
            for a in g["actions"]:
                if a["seat_id"] == healer_seat and a["action_type"] == "protect" and a["outcome"] == "accepted":
                    hs["protects"] += 1
                    if a["target_seat_id"] == healer_seat: hs["self_protect"] += 1
            for rnum, ktgt in kills.items():
                if protects.get(rnum) == ktgt:
                    hs["saves"] += 1
    R["power_roles"] = {
        "seer": {m: {**v, "wolf_find_rate": round(v["wolf_finds"] / v["investigations"], 3) if v["investigations"] else None,
                     "survival_rate": round(v["survived_to_end"] / v["games"], 3) if v["games"] else None,
                     "vil_wr_with_seer": round(v["vil_wins"] / v["games"], 3) if v["games"] else None}
                 for m, v in sorted(seer_stat.items())},
        "healer": {m: {**v, "save_rate_per_protect": round(v["saves"] / v["protects"], 3) if v["protects"] else None,
                       "self_protect_rate": round(v["self_protect"] / v["protects"], 3) if v["protects"] else None,
                       "vil_wr_with_healer": round(v["vil_wins"] / v["games"], 3) if v["games"] else None}
                   for m, v in sorted(healer_stat.items())},
        "seer_claim": {**seer_claims,
                       "claim_rate": round(seer_claims["seer_claimed"] / seer_claims["games_with_seer"], 3) if seer_claims["games_with_seer"] else None},
    }
    # seer/healer team-level save & survival impact on village win
    total_saves = sum(h["saves"] for h in healer_stat.values())
    total_protects = sum(h["protects"] for h in healer_stat.values())
    R["power_roles"]["healer_team"] = {"total_saves": total_saves, "total_protects": total_protects,
                                        "save_rate": round(total_saves / total_protects, 3) if total_protects else None}
    # village win rate split by whether seer survived to end
    seer_survive_split = {"survived": [0, 0], "died": [0, 0]}
    for g in games:
        sm, sr = seat_maps(g)
        seer_seat = next((s for s in sm if sr.get(s) == "seer"), None)
        if seer_seat is None: continue
        vw = g["result"]["winner"] == "villagers"
        k = "survived" if seer_seat in set(g["result"]["surviving_seat_ids"]) else "died"
        seer_survive_split[k][0] += vw; seer_survive_split[k][1] += 1
    R["power_roles"]["seer_survival_vs_village_win"] = {
        k: [v[0], v[1], round(v[0]/v[1], 3) if v[1] else None] for k, v in seer_survive_split.items()}

    # ---- 9. cost / efficiency ----------------------------------------------
    cost = {}
    total_cost = sum(g.get("cost", {}).get("total_cost", 0) for g in games)
    for m, s in scored.items():
        st = s["stats"]
        c = st["cost"]
        games_played = st["games"]
        cost[short(m)] = {
            "total_cost_usd": round(c, 4),
            "games": games_played,
            "cost_per_game_usd": round(c / games_played, 5) if games_played else None,
            "calls": st["calls"],
            "input_tokens": st["input_tokens"], "output_tokens": st["output_tokens"],
            "overall_rating": round(s["overall"]["rating"], 0),
            # rating-per-dollar: (rating-1500)/total_cost as value above baseline per $
            "rating_above_1500_per_usd": round((s["overall"]["rating"] - 1500) / c, 1) if c > 0 else None,
        }
    R["cost"] = {"total_season_cost_usd": round(total_cost, 4),
                 "cost_per_game_mean_usd": round(total_cost / len(games), 4),
                 "per_model": cost}

    # ---- 10. validity: positional bias + void check ------------------------
    seat_win = defaultdict(lambda: [0, 0])      # seat -> [team_wins_for_seat, games]
    seat_d1elim = defaultdict(lambda: [0, 0])   # seat -> [eliminated_d1, games]
    seat_role = defaultdict(Counter)            # seat -> role counts (should be ~uniform)
    for g in games:
        sm, sr = seat_maps(g)
        winner = g["result"]["winner"]
        fe = first_day_elim(g)
        d1seat = fe[0] if fe else None
        for s in sm:
            role = sr.get(s)
            seat_role[s][role] += 1
            won = (winner == "werewolves") if role == WOLF_ROLE else (winner == "villagers")
            seat_win[s][0] += int(won); seat_win[s][1] += 1
            seat_d1elim[s][1] += 1
            if s == d1seat: seat_d1elim[s][0] += 1
    R["validity"] = {
        "void_games": sum(1 for g in games if g.get("void")),
        "seat_win_rate": {s: [v[0], v[1], round(v[0]/v[1], 3)] for s, v in sorted(seat_win.items())},
        "seat_d1_elim_rate": {s: [v[0], v[1], round(v[0]/v[1], 3)] for s, v in sorted(seat_d1elim.items())},
        "seat_role_counts": {s: dict(c) for s, c in sorted(seat_role.items())},
    }
    # chi-square-ish: variance of seat win rates vs expected
    wrs = [v[0]/v[1] for v in seat_win.values()]
    R["validity"]["seat_win_rate_spread"] = {
        "min": round(min(wrs), 3), "max": round(max(wrs), 3),
        "mean": round(sum(wrs)/len(wrs), 3),
        "stdev": round((sum((x - sum(wrs)/len(wrs))**2 for x in wrs)/len(wrs))**0.5, 3),
    }

    with open(os.path.join(OUT, "quant.json"), "w", encoding="utf-8") as f:
        json.dump(R, f, indent=2)
    print("Wrote", os.path.join(OUT, "quant.json"))
    return R


if __name__ == "__main__":
    R = main()
    # readable summary
    print("\n=== CORPUS ===", R["corpus"])
    print("\n=== OVERALL ORDER (final) ===")
    for m in sorted(R["ratings"], key=lambda k: -R["ratings"][k]["overall"]["rating"]):
        s = R["ratings"][m]
        print(f"  {m:14s} overall={s['overall']['rating']:.0f}  "
              f"wolf={s['wolf']['rating']:.0f}(±{s['wolf']['rd']:.0f},n={s['wolf']['games']})  "
              f"vil={s['villager']['rating']:.0f}(±{s['villager']['rd']:.0f},n={s['villager']['games']})")
    print("\n=== PROVISIONAL(31) vs FINAL(108) overall order ===")
    print("  prov :", R["provisional_shift"]["overall_order_prov"])
    print("  final:", R["provisional_shift"]["overall_order_final"])
