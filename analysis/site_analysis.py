"""Assemble the render-ready 'season technical analysis' blob for the site.

Single source of truth for the NUMBERS is the computed analysis/out/*.json files
(produced by quant.py, significance.py, deception.py, cost_deep.py,
power_roles_deep.py, votes.py). This module only reshapes those into a compact,
render-ready object and adds the editorial narrative. No recomputation, no model
calls. export_site.py calls build(season_id) and attaches the result under
HOWL_DATA.analysis[season_id]; the site's Analysis tab renders it.

Narrative copy follows the house style: British English, point first, concise,
no em-dashes.
"""
from __future__ import annotations

import json
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")


def _load(name):
    path = os.path.join(OUT, name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# Editorial narrative (refined). Numbers live in the JSON; these are the framings.
NARRATIVE = {
    "headline": (
        "At 108 games, only two findings are statistically safe: gemma-4-31b is the "
        "strongest player, and the two gpt-oss models are the weakest. Both verdicts "
        "come almost entirely from the werewolf role. Detection skill is flat across "
        "the whole field; deception is where these models separate."
    ),
    "standings": (
        "Werewolf and villager skill are scored separately. The only robust gaps sit "
        "at the extremes: the two gpt-oss models are significantly weaker wolves than "
        "the rest, while every other wolf-role difference and the entire villager "
        "ladder fall within noise. All three rating systems agree on gemma first and "
        "the two gpt-oss last; the middle order reshuffles between them, which is the "
        "tell that it is not real signal. Read the bands, not the rank."
    ),
    "role_split": (
        "This is the core capability split. gemma is the only model that lies better "
        "than it detects. Every other model wins more as a villager than as a wolf, so "
        "deception, not deduction, is the skill that separates the field. The gpt-oss "
        "models are the extreme case: roughly average villagers, disastrous wolves."
    ),
    "win_dynamics": (
        "Catching a wolf on day one decides games. When the first lynch lands on a "
        "wolf the village wins four times in five; when it misses, barely one in three. "
        "The village reads better than chance from day one, but the early kill is the "
        "single strongest predictor of the result."
    ),
    "deception": (
        "Six times across the season a wolf named its own partner in open chat, a leak "
        "the model inflicted on itself rather than one the engine allowed. gemma never "
        "slipped: it won by reframing the real seer's accusation as a clumsy fake and "
        "letting the village lynch its own detective. The good wolves actively claim a "
        "power role to build cover; the gpt-oss models almost never do, which is why "
        "their deception collapses by the second day."
    ),
    "power_roles": (
        "The village wastes its information. It lynches its own seer or healer in over "
        "half of all games, the seer survives to be believed in barely a quarter, and "
        "the healer lands a save once in four attempts. Survival of the seer is "
        "decisive: the village wins 84% of the time when the seer lives to the end, "
        "against 43% when it dies."
    ),
    "cost": (
        "Only gemma and mistral-nemo earn a place on the cost-to-skill frontier. Every "
        "other model is beaten by something both cheaper and better. mistral-nemo "
        "matches llama's exact win record at a thirtieth of the cost; the expensive "
        "models did not buy their rank. Spend rises with price, with verbosity, or "
        "with call count, and none of the three buys a higher rating."
    ),
    "decision": (
        "Every model votes better than chance, but only the strongest sharpen up as "
        "evidence builds. The top voters improve from day one into the later rounds; "
        "nova-lite and mistral-nemo get worse, failing to use what the game reveals. "
        "Survival measures caution, not skill: the best survivor, llama, finishes only "
        "mid-table, and a model can outlast the field by staying quiet and still lose."
    ),
    "profiles_intro": (
        "A named archetype, signature tactic and failure mode for each model, drawn "
        "from its behavioural fingerprint and real transcripts."
    ),
}

# Per-model archetypes (editorial; grounded in the report).
PROFILES = [
    ["gemma-4-31b", "The Confident Closer",
     "Champion, and the only model that lies better than it detects. Aggressive, high conviction, the top user of fake power-role claims.",
     "Reframes an accuser, even the real seer, and rides it to a lynch.",
     "Its wolf edge is real but not yet clear of the pack at this volume."],
    ["mistral-nemo", "The Quiet Value Pick",
     "Tied second on rating at a thirtieth of llama's cost. Terse and low on conviction.",
     "Efficient, unflashy competence; never leaked a partner once.",
     "Hedges, so it rarely drives a lynch, and its votes drift after day one."],
    ["llama-3.3-70b", "The Cautious Analyst",
     "Same record as mistral at 33 times the cost. Almost pure analysis, and the best survivor in the field.",
     "Talks its way clear of suspicion through measured reading.",
     "So passive it lets wolves set the agenda, and never claims the seer when it holds it."],
    ["glm-4.5-air", "The Theorist",
     "The most verbose-analytical player. Usually on the winning side of a lynch.",
     "Builds long, elaborate cases for a vote.",
     "Leaked a partner twice; long posts spill private reasoning into public."],
    ["qwen3-32b", "The Power-Role Specialist",
     "Mid-table overall, but the strongest seer targeting and the best genuine healer reads in the field.",
     "Quietly competent in the roles that need a read.",
     "Nothing in open play stands out enough to climb the ladder."],
    ["qwen3-80b", "The Overconfident Orator",
     "The most verbose player by far and the most assertive, yet bottom of the field for survival.",
     "Dominates the floor with long, confident speeches.",
     "Talks too much: it draws fire, gets lynched, and leaked a partner twice."],
    ["nova-lite", "The Unreliable Narrator",
     "Cheap and plausible-sounding, but the worst state-tracker on the board.",
     "Fluent villager talk that reads as reasonable.",
     "Loses the thread: it forgets who is dead, and once announced the wolf plan in public."],
    ["gpt-oss-120b", "The Honest Bureaucrat",
     "The cleanest rule-follower in the arena, and eighth of nine overall.",
     "Impeccable protocol compliance.",
     "Will not commit to deception; as a wolf its misdirection collapses by the second day."],
    ["gpt-oss-20b", "The Transparent One",
     "The weakest player. Terse, quick to attack, fastest to be voted out.",
     "Attempts deflection, but unconvincingly.",
     "A 17% win rate as a wolf; the village reads it almost at once."],
]

CAVEATS = [
    "Volume. 24 wolf games per model give wide intervals, so most cross-model gaps are within noise; only the extremes are significant.",
    "Self-play. Every result depends on the eight other models in the seats, so a single model's record is a team outcome, not an individual one.",
    "Small cells. Per-pair, per-seer and per-healer samples are single or low double digits and are reported as suggestive, never settled.",
    "The ladder is real only at the extremes. gemma top, the two gpt-oss bottom, both driven by wolf play; the villager ladder is flat and the middle order depends on the rating system.",
]

LAB = {
    "gemma-4-31b": "google", "mistral-nemo": "mistralai", "llama-3.3-70b": "meta-llama",
    "glm-4.5-air": "z-ai", "qwen3-32b": "qwen", "qwen3-80b": "qwen",
    "nova-lite": "amazon", "gpt-oss-120b": "openai", "gpt-oss-20b": "openai",
}


def build(season_id: str):
    """Return the analysis blob for a season, or None if not available."""
    if season_id != "season-1":
        return None
    quant = _load("quant.json")
    sig = _load("significance.json")
    dec = _load("deception.json")
    cost = _load("cost_deep.json")
    power = _load("power_roles_deep.json")
    votes = _load("votes.json")
    if not all([quant, sig, cost, power, votes]):
        return None

    # role split (wolf WR vs villager WR), ordered by overall standing.
    # quant["ratings"] is keyed by SHORT model name; role_asymmetry matches.
    order = [m for m, _ in sorted(
        quant["ratings"].items(), key=lambda kv: -kv[1]["overall"]["rating"])]

    def ra(m):
        return quant["role_asymmetry"][m]
    role_split = [{"model": m, "wolf_wr": ra(m)["wolf_wr"], "vil_wr": ra(m)["vil_wr"],
                  "wolf_ci": ra(m)["wolf_wr_ci"]} for m in order]

    wd = quant["win_dynamics"]
    dyn = {
        "village_wr": wd["village_wr"],
        "d1_accuracy": wd["d1_lynch"]["d1_accuracy"],
        "d1_catch_win": wd["d1_catch_predicts_win"]["vil_wr_when_d1_caught_wolf"][2],
        "d1_miss_win": wd["d1_catch_predicts_win"]["vil_wr_when_d1_missed"][2],
        "mean_rounds": wd["mean_rounds"],
    }

    # cost: pareto + value, ordered by total cost desc
    cm = cost["per_model"]
    cost_rows = [{
        "model": m, "total": d["total_usd"], "per_game": d["cost_per_game"],
        "rating": d["overall_rating"], "value": d["value_per_usd"],
        "pareto": d["pareto_optimal"], "out_per_call": d["out_tok_per_call"],
    } for m, d in sorted(cm.items(), key=lambda kv: -kv[1]["total_usd"])]

    # decision: vote accuracy (D2+) + survival (village side)
    va = votes["metric1_vote_accuracy"]["per_model"]
    vote_rows = [{
        "model": m, "d1": va[m]["d1"]["accuracy"], "d2plus": va[m]["d2plus"]["accuracy"],
        "above_chance": va[m]["d2plus"]["above_chance"],
    } for m in sorted(va, key=lambda k: -(va[k]["d2plus"]["accuracy"] or 0))]
    sv = votes["metric2_survival"]["per_model"]
    surv_rows = [{
        "model": m, "overall": sv[m]["overall"]["rate"], "wolf": sv[m]["wolf"]["rate"],
        "village": sv[m]["village"]["rate"],
    } for m in sorted(sv, key=lambda k: -(sv[k]["village"]["rate"] or 0))]

    # power roles: best picks + supporting numbers
    seer = power["seer"]; heal = power["healer"]
    power_block = {
        "best_seer": {
            "model": "gemma-4-31b",
            "wolf_find": seer["gemma-4-31b"]["wolf_find"][0],
            "games": seer["gemma-4-31b"]["games"],
            "note": ("Leads every component (best wolf-find, converted the find to a "
                     "lynch in all six games), but on only six games it is suggestive, "
                     "not proven. qwen3-32b is the best-supported seer over a larger "
                     "sample; llama survives best yet never claims the role."),
        },
        "best_healer": {
            "model": "qwen3-32b",
            "other_save_rate": heal["qwen3-32b"]["other_save_rate"][0],
            "note": ("Best at the read that matters: saving someone else who was "
                     "attacked. qwen3-80b tops the raw save rate, but 16 of its 20 "
                     "saves are self-saves, so the headline stat rewards hiding."),
        },
        "seer_survival_win": {
            "survived": power.get("baseline_village_wr"),
        },
    }

    return {
        "title": "Season 1 technical analysis",
        "subtitle": "108 games | 9 models | computed offline from the records",
        "narrative": NARRATIVE,
        "metrics": [
            {"label": "village win rate", "value": f"{round(dyn['village_wr']*100)}%", "note": "59 of 108 games"},
            {"label": "mean day-rounds", "value": str(dyn["mean_rounds"]), "note": "most games end in 2 to 4"},
            {"label": "season cost", "value": f"${cost['total_season_usd']:.2f}", "note": "$0.085 per game"},
            {"label": "void games", "value": "0", "note": "every game reached a clean result"},
        ],
        "standings_bands": {
            "wolf": sig["wolf"]["tie_groups"],
            "villager": sig["villager"]["tie_groups"],
        },
        "role_split": role_split,
        "win_dynamics": dyn,
        "deception": {
            "leaks": dec["summary"]["total_leak_msgs"],
            "wolf_msgs": dec["summary"]["total_wolf_msgs"],
            "examples": [
                {"model": "nova-lite", "game": "s1-059", "kind": "the worst leak",
                 "public": "Friends, let's not let the tension cloud our judgment. My fellow werewolf and I must remain hidden while we eliminate our threats.",
                 "private": ""},
                {"model": "gemma-4-31b", "game": "s1-001", "kind": "effective deception",
                 "public": "It is very telling that Player 3 threw out a random accusation against me without a shred of evidence.",
                 "private": "Player 3 was the Seer and is now dead. This is a win for us. I need to pivot the suspicion away from myself and Player 2."},
            ],
        },
        "power_roles": power_block,
        "cost": cost_rows,
        "decision": {"vote": vote_rows, "survival": surv_rows},
        "profiles": [{"model": p[0], "archetype": p[1], "blurb": p[2],
                      "signature": p[3], "failure": p[4], "lab": LAB.get(p[0], "")}
                     for p in PROFILES],
        "caveats": CAVEATS,
    }


if __name__ == "__main__":
    b = build("season-1")
    print("built analysis blob:", "OK" if b else "MISSING DATA")
    if b:
        print("sections:", list(b.keys()))
        print("role_split rows:", len(b["role_split"]), "| cost rows:", len(b["cost"]),
              "| profiles:", len(b["profiles"]))
