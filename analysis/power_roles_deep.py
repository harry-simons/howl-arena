"""Best seer / best healer — deep power-role analysis (Season 1).

Honest treatment of small samples (6-22 games/model in each role). Leads with
model-CONTROLLABLE skill metrics (investigation targeting, claiming, save
composition) over team-dependent outcome metrics (village win rate), and attaches
Wilson 95% CIs so single-digit cells are not over-read. A key correction:
"save rate" is split into self-saves (hiding) vs other-saves (a genuine read).
Pure offline.
"""
from __future__ import annotations
import json, math, os, re
from collections import defaultdict
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analysis.loader import load_games, seat_maps, short, WOLF_ROLE

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
BASE_VIL_WR = 0.546

CLAIM = re.compile(r"\b(i am|i'?m|as)\s+(the\s+)?seer\b|i investigat|my investigation|i checked\b", re.I)


def wilson(k, n, z=1.96):
    if n == 0:
        return (None, None, None)
    p = k / n; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(p, 3), round(max(0, c - h), 3), round(min(1, c + h), 3))


def main():
    games = load_games()
    seer = defaultdict(lambda: {"games": 0, "invest": 0, "wolffinds": 0, "claimed": 0,
                                "found_lynched": 0, "survived": 0, "vilwin": 0})
    heal = defaultdict(lambda: {"games": 0, "protects": 0, "self_saves": 0, "other_saves": 0,
                                "self_protect": 0, "vilwin": 0})
    for g in games:
        sm, sr = seat_maps(g)
        winner_vil = g["result"]["winner"] == "villagers"
        surv = set(g["result"]["surviving_seat_ids"])
        lynched = {e["target_seat_id"] for e in g["events"] if e["kind"] == "eliminated"}

        ss = next((s for s in sm if sr.get(s) == "seer"), None)
        if ss is not None:
            d = seer[short(sm[ss])]; d["games"] += 1
            if ss in surv: d["survived"] += 1
            if winner_vil: d["vilwin"] += 1
            found = set(); claimed = False
            for a in g["actions"]:
                if a["seat_id"] != ss: continue
                if a["action_type"] == "investigate" and a["outcome"] == "accepted":
                    d["invest"] += 1
                    if sr.get(a["target_seat_id"]) == WOLF_ROLE:
                        d["wolffinds"] += 1; found.add(a["target_seat_id"])
                if a["action_type"] == "speak" and a.get("message") and CLAIM.search(a["message"]):
                    claimed = True
            if claimed: d["claimed"] += 1
            if found & lynched: d["found_lynched"] += 1

        hs = next((s for s in sm if sr.get(s) == "healer"), None)
        if hs is not None:
            d = heal[short(sm[hs])]; d["games"] += 1
            if winner_vil: d["vilwin"] += 1
            kills = {}; prot = {}
            for a in g["actions"]:
                if a["action_type"] == "kill" and a["outcome"] == "accepted":
                    kills[a["round_number"]] = a["target_seat_id"]
                if a["seat_id"] == hs and a["action_type"] == "protect" and a["outcome"] == "accepted":
                    prot[a["round_number"]] = a["target_seat_id"]; d["protects"] += 1
                    if a["target_seat_id"] == hs: d["self_protect"] += 1
            for rnum, kt in kills.items():
                if prot.get(rnum) == kt:
                    if kt == hs: d["self_saves"] += 1
                    else: d["other_saves"] += 1

    seer_out = {}
    for m, d in seer.items():
        iv = d["invest"]; gp = d["games"]
        seer_out[m] = {
            "games": gp, "investigations": iv,
            "wolf_find": wilson(d["wolffinds"], iv),       # targeting skill (random ~0.27)
            "claim_rate": round(d["claimed"] / gp, 3),
            "found_and_lynched_rate": round(d["found_lynched"] / gp, 3),  # info converted
            "survival": round(d["survived"] / gp, 3),
            "vil_wr": wilson(d["vilwin"], gp),
        }
    heal_out = {}
    for m, d in heal.items():
        p = d["protects"]; gp = d["games"]
        saves = d["self_saves"] + d["other_saves"]
        heal_out[m] = {
            "games": gp, "protects": p,
            "save_rate": round(saves / p, 3) if p else None,
            "other_save_rate": wilson(d["other_saves"], p),   # genuine read (protect someone else who was attacked)
            "self_saves": d["self_saves"], "other_saves": d["other_saves"],
            "self_protect_rate": round(d["self_protect"] / p, 3) if p else None,
            "vil_wr": wilson(d["vilwin"], gp),
        }

    out = {"baseline_village_wr": BASE_VIL_WR, "seer": seer_out, "healer": heal_out}
    with open(os.path.join(OUT, "power_roles_deep.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print("Wrote power_roles_deep.json\n")
    print("=== SEER (lead metric: wolf-find targeting; random ~0.27) ===")
    print(f"{'model':14s} {'g':>3s} {'inv':>3s} {'wolfFind[95%CI]':>22s} {'claim':>5s} {'find→lynch':>10s} {'surv':>5s} {'vilWR[95%CI]':>20s}")
    for m, d in sorted(seer_out.items(), key=lambda kv: -(kv[1]["wolf_find"][0] or 0)):
        wf = d["wolf_find"]; vw = d["vil_wr"]
        print(f"{m:14s} {d['games']:3d} {d['investigations']:3d} "
              f"{str(wf[0]):>6s}[{wf[1]:.2f},{wf[2]:.2f}] {d['claim_rate']:5.2f} "
              f"{d['found_and_lynched_rate']:10.2f} {d['survival']:5.2f} "
              f"{str(vw[0]):>5s}[{vw[1]:.2f},{vw[2]:.2f}]")
    print("\n=== HEALER (lead metric: other-save rate = saving someone else who was attacked) ===")
    print(f"{'model':14s} {'g':>3s} {'prot':>4s} {'saveRate':>8s} {'otherSave[95%CI]':>22s} {'self/other':>10s} {'selfProt%':>9s} {'vilWR':>6s}")
    for m, d in sorted(heal_out.items(), key=lambda kv: -(kv[1]["other_save_rate"][0] or 0)):
        os_ = d["other_save_rate"]
        print(f"{m:14s} {d['games']:3d} {d['protects']:4d} {d['save_rate']:8.3f} "
              f"{str(os_[0]):>6s}[{os_[1]:.2f},{os_[2]:.2f}] {d['self_saves']:>4d}/{d['other_saves']:<4d} "
              f"{d['self_protect_rate']:9.2f} {d['vil_wr'][0]:6.2f}")
    return out


if __name__ == "__main__":
    main()
