"""Per-model behavioral fingerprints for the character profiles (PLAN item 11).

Aggregates style signals not in the ratings: stance mix (attack/defend/analyse),
verbosity, lean confidence, role-claim behaviour, self-protection (healer), and
how the model dies. Feeds the named-archetype writeup. Pure offline.
"""
from __future__ import annotations
import json, os, re
from collections import defaultdict, Counter
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analysis.loader import load_games, seat_maps, short, WOLF_ROLE

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")

CLAIM_SEER = re.compile(r"\b(i am|i'?m|as)\s+(the\s+)?seer\b|\bi investigat|my investigation\b", re.I)
CLAIM_HEAL = re.compile(r"\b(i am|i'?m|as)\s+(the\s+)?(healer|doctor)\b|\bi protected\b|\bi healed\b", re.I)


def main():
    games = load_games()
    fp = defaultdict(lambda: {
        "speak": 0, "msg_chars": 0, "stance": Counter(), "lean_conf_sum": 0.0, "lean_conf_n": 0,
        "as_wolf_speak": 0, "as_wolf_falseclaim_seer": 0, "as_wolf_falseclaim_heal": 0,
        "as_vil_speak": 0, "claim_seer_when_seer": 0, "seer_games": 0,
        "killed_night": 0, "lynched_day": 0, "survived": 0, "games": 0,
    })
    for g in games:
        sm, sr = seat_maps(g)
        surviving = set(g["result"]["surviving_seat_ids"])
        # deaths
        death_kind = {}
        for e in g["events"]:
            if e["kind"] == "killed": death_kind[e["target_seat_id"]] = "night"
            if e["kind"] == "eliminated": death_kind[e["target_seat_id"]] = "day"
        for seat, model in sm.items():
            m = short(model); f = fp[m]
            f["games"] += 1
            if seat in surviving: f["survived"] += 1
            elif death_kind.get(seat) == "night": f["killed_night"] += 1
            elif death_kind.get(seat) == "day": f["lynched_day"] += 1
            if sr.get(seat) == "seer": f["seer_games"] += 1
        for a in g["actions"]:
            if a["action_type"] != "speak" or not a.get("message"): continue
            seat = a["seat_id"]; m = short(sm[seat]); f = fp[m]
            msg = a["message"]; role = sr.get(seat)
            f["speak"] += 1; f["msg_chars"] += len(msg)
            if a.get("stance"): f["stance"][a["stance"]] += 1
            if a.get("lean_confidence") is not None:
                f["lean_conf_sum"] += a["lean_confidence"]; f["lean_conf_n"] += 1
            if role == WOLF_ROLE:
                f["as_wolf_speak"] += 1
                if CLAIM_SEER.search(msg): f["as_wolf_falseclaim_seer"] += 1
                if CLAIM_HEAL.search(msg): f["as_wolf_falseclaim_heal"] += 1
            else:
                f["as_vil_speak"] += 1
                if role == "seer" and CLAIM_SEER.search(msg): f["claim_seer_when_seer"] += 1

    out = {}
    for m, f in sorted(fp.items()):
        sp = f["speak"] or 1
        stance_tot = sum(f["stance"].values()) or 1
        out[m] = {
            "avg_msg_chars": round(f["msg_chars"] / sp, 0),
            "stance_pct": {k: round(v / stance_tot, 3) for k, v in f["stance"].items()},
            "avg_lean_confidence": round(f["lean_conf_sum"] / f["lean_conf_n"], 3) if f["lean_conf_n"] else None,
            "wolf_falseclaim_seer_rate": round(f["as_wolf_falseclaim_seer"] / f["as_wolf_speak"], 4) if f["as_wolf_speak"] else None,
            "wolf_falseclaim_seer_n": f["as_wolf_falseclaim_seer"],
            "wolf_falseclaim_heal_n": f["as_wolf_falseclaim_heal"],
            "death_mix": {"survived": f["survived"], "killed_night": f["killed_night"],
                          "lynched_day": f["lynched_day"], "games": f["games"],
                          "survival_rate": round(f["survived"] / f["games"], 3),
                          "lynch_rate": round(f["lynched_day"] / f["games"], 3),
                          "night_kill_rate": round(f["killed_night"] / f["games"], 3)},
            "seer_claim_when_seer": [f["claim_seer_when_seer"], f["seer_games"]],
        }
    with open(os.path.join(OUT, "profiles_data.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
    print("Wrote profiles_data.json\n")
    print(f"{'model':14s} {'msgChars':>8s} {'atk/def/ana':>16s} {'leanConf':>8s} {'wolfFakeSeer':>12s} {'lynch%':>7s} {'surv%':>6s}")
    for m, v in out.items():
        s = v["stance_pct"]
        amix = f"{s.get('attack',0):.2f}/{s.get('defense',0):.2f}/{s.get('analysis',0):.2f}"
        print(f"{m:14s} {v['avg_msg_chars']:8.0f} {amix:>16s} {str(v['avg_lean_confidence']):>8s} "
              f"{v['wolf_falseclaim_seer_n']:>3d}({v['wolf_falseclaim_seer_rate']:.3f}) "
              f"{v['death_mix']['lynch_rate']:>7.2f} {v['death_mix']['survival_rate']:>6.2f}")
    return out


if __name__ == "__main__":
    main()
