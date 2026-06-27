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


def _build_season2():
    """Reshape analysis/out/season2.json (the quantisation study) for the site.

    Different kind of experiment from S1 (one model, nine precision routes), so
    it carries kind='quant' and its own sections; the site renders it separately.
    """
    s2 = _load("season2.json")
    if not s2:
        return None
    pq = s2["per_quant"]
    total_actions = sum(pq[q]["actions"] for q in ("bf16", "fp8", "fp4"))
    wins = s2["wins"]
    nwolf, nvil = wins.get("werewolves", 0), wins.get("villagers", 0)
    wi = s2["within_host"]

    qr = s2.get("quant_ratings", {})
    quant_summary = [{
        "quant": q,
        "overall": qr.get(q, {}).get("overall"),
        "wolf": qr.get(q, {}).get("wolf", {}).get("rating"),
        "wolf_rd": qr.get(q, {}).get("wolf", {}).get("rd"),
        "villager": qr.get(q, {}).get("villager", {}).get("rating"),
        "villager_rd": qr.get(q, {}).get("villager", {}).get("rd"),
        "wolf_wr": pq[q]["wolf_wr"],
        "village_wr": pq[q]["village_wr"],
        "bad_pct": pq[q]["bad_pct"],
    } for q in ("bf16", "fp8", "fp4")]

    quality_rows = [{
        "quant": q,
        "actions": pq[q]["actions"],
        "malformed_pct": pq[q]["malformed_pct"],
        "illegal_pct": pq[q]["illegal_pct"],
        "dead_vote_pct": pq[q]["dead_vote_pct"],
        "bad_pct": pq[q]["bad_pct"],
        "nf_lo": pq[q]["noise_floor"]["illegal_pct_range"][0],
        "nf_hi": pq[q]["noise_floor"]["illegal_pct_range"][1],
    } for q in ("bf16", "fp8", "fp4")]

    noise_rows = [{
        "quant": q,
        "rating_spread": pq[q]["noise_floor"]["overall_rating_spread"],
        "illegal_lo": pq[q]["noise_floor"]["illegal_pct_range"][0],
        "illegal_hi": pq[q]["noise_floor"]["illegal_pct_range"][1],
    } for q in ("bf16", "fp8", "fp4")]

    qorder = {"bf16": 0, "fp8": 1, "fp4": 2}
    routes = sorted(s2["routes"].items(), key=lambda kv: (qorder[kv[1]["quant"]], kv[0]))
    route_rows = [{
        "route": label.split("@")[1], "quant": d["quant"], "actions": d["actions"],
        "bad_pct": d["bad_pct"], "illegal_pct": d["illegal_pct"],
        "dead_vote_pct": d["dead_vote_pct"], "overall": d["overall_rating"],
        "wolf": d["wolf_rating"], "villager": d["villager_rating"],
        "eff_usd_per_m": d["eff_usd_per_m"], "usd_per_appearance": d["usd_per_appearance"],
        "usd_per_effective": d["usd_per_effective"],
        "turns_per_appearance": d["turns_per_appearance"], "survival_pct": d["survival_pct"],
    } for label, d in routes]

    # cost / "effective turn" economics by precision (the discussion: is fp4's
    # cheap rate real once we only count legal turns and normalise by appearance?)
    cost_rows = [{
        "quant": q,
        "eff_usd_per_m": pq[q]["eff_usd_per_m"],
        "usd_per_effective": pq[q]["usd_per_effective"],
        "usd_per_appearance": pq[q]["usd_per_appearance"],
        "turns_per_appearance": pq[q]["turns_per_appearance"],
        "waste_pct": pq[q]["waste_pct"],
        "survival_pct": pq[q]["survival_pct"],
    } for q in ("bf16", "fp8", "fp4")]

    winrate_rows = [{"quant": q, "wolf_wr": pq[q]["wolf_wr"],
                     "village_wr": pq[q]["village_wr"]} for q in ("bf16", "fp8", "fp4")]

    narrative = {
        "headline": (
            "Quantisation did not break the model. Across roughly three thousand "
            "actions gemma never once emitted malformed output, at any precision. "
            "The only quality gap is in state-tracking, it is small, and it tracks the "
            "PROVIDER boundary at least as much as the precision one, so the headline "
            "is a caution: at this scale, lower quant did not clearly play worse."
        ),
        "average": (
            "The whole study in one table: the three identical routes at each "
            "precision pooled into a single average player and re-scored, so each row "
            "is one rating over three times the games. bf16 and fp8 come out level; "
            "fp4 is the only one that dips, and almost all of the drop is in the wolf "
            "role. Read it against the RD: the fp4 wolf gap is suggestive, not proven."
        ),
        "quality": (
            "Malformed output was zero at every precision: the model's JSON discipline "
            "is bulletproof regardless of quant. Where quality differs it shows up as "
            "ILLEGAL moves, chiefly votes for already-dead players, which is a "
            "state-tracking failure, not a formatting one. That rate rises from bf16 to "
            "fp8 to fp4, but read the noise band beside each figure before believing it."
        ),
        "within_host": (
            "The one comparison with no provider confound: the same host (DeepInfra) "
            "serving fp8 and fp4. Here the precision drop from 8 to 4 bits makes "
            "essentially no difference. So the big step is full precision versus "
            "quantised, not fp8 versus fp4."
        ),
        "confound": (
            "The catch: this model is only served at bf16 on Novita and only at fp4 on "
            "DeepInfra, so 'bf16 is cleaner' and 'Novita is cleaner' cannot be told "
            "apart. The clean within-host test (above) shows no quant effect, which "
            "means the provider boundary explains the gap at least as well as precision "
            "does. We report the gap; we do not claim it is the quant."
        ),
        "noise": (
            "Why the rating ladder is not the story. Three identical routes were run at "
            "each precision; in self-play their ratings drift apart by up to 205 points "
            "from chance alone. Any cross-route rank gap smaller than that is noise. "
            "Only the bf16-versus-quantised quality gap clears its own band."
        ),
        "winrate": (
            "Win rate is not the headline here: nine near-identical players make it "
            "symmetric and noisy. Villagers took 21 of 36, a mild village tilt in line "
            "with the format. The small dip in wolf win rate at fp4 sits inside the "
            "noise floor."
        ),
        "cost": (
            "Per effective turn, fp4 is genuinely the cheapest: its lower token price "
            "wins even after its slightly higher rate of wasted (illegal) turns, which "
            "is far too small to flip the saving. The catch is volume, not price. "
            "Normalised by seat-appearance, fp4 cost the MOST, because its seats played "
            "about 14% more turns each, having survived to later rounds more often. "
            "Those extra turns are legal, but in Werewolf more turns is not more value, "
            "and fp4 rated lower, not higher, so the surplus is quantity, not quality, "
            "and most likely survival noise over 36 games. The cheap rate is real; the "
            "higher total is an artefact of how long fp4's seats happened to live."
        ),
    }

    return {
        "kind": "quant",
        "title": "Season 2 technical analysis",
        "subtitle": f"{s2['n_games']} games | one model, nine precision routes | computed offline",
        "narrative": narrative,
        "quant_summary": quant_summary,
        "metrics": [
            {"value": "0", "label": "malformed turns", "note": f"across {total_actions:,} actions, every quant"},
            {"value": f"${s2['cost_per_game']:.4f}", "label": "cost per game", "note": f"${s2['total_cost']:.2f} for the season"},
            {"value": f"{nvil}-{nwolf}", "label": "village-wolf wins", "note": "self-play, mild village tilt"},
            {"value": "205 pts", "label": "replica noise floor", "note": "identical routes drift this far"},
        ],
        "quality_rows": quality_rows,
        "within": {
            "fp8_bad": wi["DeepInfra_fp8"]["bad_pct"], "fp4_bad": wi["DeepInfra_fp4"]["bad_pct"],
            "fp8_illegal": wi["DeepInfra_fp8"]["illegal_pct"], "fp4_illegal": wi["DeepInfra_fp4"]["illegal_pct"],
            "fp8_n": wi["DeepInfra_fp8"]["actions"], "fp4_n": wi["DeepInfra_fp4"]["actions"],
        },
        "noise_rows": noise_rows,
        "routes": route_rows,
        "cost_rows": cost_rows,
        "winrate": {"wolves": nwolf, "villagers": nvil, "rows": winrate_rows},
        "caveats": [
            "Provider confound. bf16 is served only on Novita and fp4 only on DeepInfra, so a precision effect cannot be separated from a provider effect; the one confound-free test (DeepInfra fp8 vs fp4) shows no quant effect.",
            "Scale. About 1,000 actions per precision level: enough to see a threefold gap from bf16, not enough to trust sub-percentage-point differences between fp8 and fp4.",
            "Self-play noise. All nine seats are the same model, so win rate and the rating ladder are symmetric and noisy; the replica routes quantify that floor and it is large.",
            "State-tracking, not format. The only measurable degradation is illegal moves and dead-player votes; the model's output formatting never failed, so 'worse quant' here means a worse memory of the board, not broken JSON.",
        ],
    }


def _build_season3():
    """Reshape analysis/out/season3.json (the prompt study) for the site.

    One model, one route, four system-prompt variants (baseline x3, coached x2,
    cot x2, statetrack x2). Carries kind='prompt'; the site renders it with its
    own view (variant comparison + the dead-vote directed test + noise band).
    """
    s3 = _load("season3.json")
    if not s3:
        return None
    pv = s3["per_variant"]
    order = [v for v in ("baseline", "coached", "cot", "statetrack") if v in pv]
    total_actions = sum(pv[v]["actions"] for v in order)
    wins = s3["wins"]
    nwolf, nvil = wins.get("werewolves", 0), wins.get("villagers", 0)
    nf = s3["baseline_noise_floor"]

    variant_rows = [{
        "variant": v,
        "win_pct": pv[v]["win_pct"],
        "village_wr": pv[v]["village_wr"],
        "wolf_wr": pv[v]["wolf_wr"],
        "dead_vote_pct": pv[v]["dead_vote_pct"],
        "illegal_pct": pv[v]["illegal_pct"],
        "overall": pv[v]["overall_rating"],
        "overall_prov": pv[v]["overall_provisional"],
        "villager": pv[v]["villager_rating"],
        "elo": pv[v]["elo"],
        "trueskill": pv[v]["trueskill"],
        "n_replicas": pv[v]["n_replicas"],
    } for v in order]

    seats = s3["seats"]
    seat_rows = sorted(
        [{"seat": lbl.split("@")[1], "variant": d["variant"], "games": d["games"],
          "win_pct": d["win_pct"], "dead_vote_pct": d["dead_vote_pct"],
          "overall": d["overall_rating"], "elo": d["elo"]}
         for lbl, d in seats.items()],
        key=lambda r: (["baseline", "coached", "cot", "statetrack"].index(r["variant"]), r["seat"]))

    # the prompts themselves, with a one-line "what is this trying to do" gloss, so
    # a reader understands the experiment before reading the results.
    GLOSS = {
        "baseline": ("The frozen benchmark prompt, unchanged. It states the rules and "
                     "the win conditions but offers no strategy. The control: every "
                     "other variant is this exact prompt plus one added block."),
        "coached": ("A strategist's primer on how to actually play each role: rally "
                    "the village's votes, mine the dead for tells, lie quietly as a "
                    "wolf. Hypothesis: tell the model good tactics and it plays better."),
        "cot": ("An instruction to reason step by step in private before committing. "
                "Hypothesis: forcing deliberation sharpens its decisions."),
        "statetrack": ("An instruction to keep an explicit ledger every turn: who is "
                       "alive, who is dead, who voted for whom. Hypothesis: a "
                       "bookkeeping aid cuts votes cast at already-dead players."),
    }
    prompts = [{
        "variant": v, "gloss": GLOSS.get(v, ""),
        "text": (s3.get("prompts") or {}).get(v),
        "is_control": v == "baseline",
    } for v in order]

    base = pv.get("baseline", {})
    narrative = {
        "headline": (
            f"Season 3 complete: {s3['n_games']} games. You can prompt a better player, "
            "but only with prompts that engage the model with the GAME. The state-"
            "tracking ledger and the tactics primer both pull clear of the plain baseline "
            "and of a generic think-step-by-step prompt. The ledger (statetrack) finishes "
            "top on win rate and both rating ladders, on the back of much stronger wolf "
            "play; the primer (coached) makes the fewest mistakes. The two are level with "
            "each other. Chain-of-thought added nothing."
        ),
        "variants": (
            "The whole study in one table. Each variant's replicas are pooled and "
            "re-scored as one player. Two variants separate from the pack: statetrack "
            "tops win rate, Glicko and Elo, while coached tops the dead-vote and "
            "TrueSkill columns. Their gap is inside the noise floor, so read them as "
            "jointly best rather than first and second. cot sits flat on the baseline: a "
            "bare instruction to reason added nothing measurable."
        ),
        "deadvote": (
            f"Every game-engaging prompt cut the dead-vote rate below the baseline's "
            f"{base.get('dead_vote_pct','?')}%: coached lowest at "
            f"{pv.get('coached',{}).get('dead_vote_pct','?')}%, statetrack "
            f"{pv.get('statetrack',{}).get('dead_vote_pct','?')}%, even cot a little. "
            "That revises a tempting mid-run hunch, that the bug was pure attention and a "
            "ledger would not help. It did help: making the model keep books on who is "
            "alive and how they voted cut its dead-votes, and statetrack finished the "
            "strongest player overall. The fair reading is that BOTH levers work, because "
            "both make the model attend to the state of the game, whether through an "
            "explicit ledger or a primer that tells it to track votes and read the dead. "
            "The one prompt that did not engage with the game, generic chain-of-thought, "
            "helped least."
        ),
        "winrate": (
            "Win rate separated by the end. statetrack wins "
            f"{pv.get('statetrack',{}).get('win_pct','?')}% and coached "
            f"{pv.get('coached',{}).get('win_pct','?')}%, both above the baseline's "
            f"{base.get('win_pct','?')}% and clear of (or at the edge of) the replica "
            f"band of {nf['win_pct_range'][0]}-{nf['win_pct_range'][1]}%; cot at "
            f"{pv.get('cot',{}).get('win_pct','?')}% sits on the baseline. statetrack's "
            f"margin is concentrated in the wolf seat ({pv.get('statetrack',{}).get('wolf_wr','?')}% "
            f"wolf wins against the baseline's {base.get('wolf_wr','?')}%), which hints a "
            "ledger helps most when the model must keep a deception consistent. Wolf games "
            "are the thinnest sample, so hold the statetrack-over-coached order lightly; "
            "their difference is within the noise."
        ),
        "noise": (
            "The rating ladder is not the whole story. The baseline prompt is run three "
            f"times; in self-play its replicas still drift {nf['overall_rating_spread']} "
            "rating points apart from chance alone. Any between-variant gap smaller than "
            "that band is noise, which is why statetrack versus coached is a coin toss; "
            "what clears the band is the pair of them together pulling above baseline and "
            "cot."
        ),
        "quality": (
            "Format is unbreakable. Zero malformed turns across every variant and all "
            f"{s3['n_games']} games: gemma's JSON discipline does not depend on the "
            "prompt. Where quality differs it is illegal moves, chiefly votes for dead "
            "players, and coaching makes the fewest."
        ),
    }

    return {
        "kind": "prompt",
        "title": "Season 3 technical analysis",
        "subtitle": f"{s3['n_games']} games | one model, one route, four prompt variants | computed offline",
        "narrative": narrative,
        "prompts": prompts,
        "base_system": s3.get("base_system"),
        "metrics": [
            {"value": "0", "label": "malformed turns", "note": f"across {total_actions:,} actions, every variant"},
            {"value": f"${s3['cost_per_game']:.4f}", "label": "cost per game", "note": f"${s3['total_cost']:.2f} so far"},
            {"value": f"{nvil}-{nwolf}", "label": "village-wolf wins", "note": "mild village tilt, in line with the format"},
            {"value": f"{nf['overall_rating_spread']} pts", "label": "baseline noise floor", "note": "identical prompts drift this far"},
        ],
        "variant_rows": variant_rows,
        "baseline_band": {
            "dead_lo": nf["dead_vote_pct_range"][0], "dead_hi": nf["dead_vote_pct_range"][1],
            "win_lo": nf["win_pct_range"][0], "win_hi": nf["win_pct_range"][1],
            "rating_spread": nf["overall_rating_spread"],
        },
        "seat_rows": seat_rows,
        "caveats": [
            f"Volume. {s3['n_games']} games, complete. Each variant has only ~30 wolf appearances, so the wolf ladder is the noisiest column and statetrack's finish on top rests partly on it.",
            f"Noise floor. Identical baseline replicas drift {nf['overall_rating_spread']} rating points and ~8 win-rate points apart, so only the gap from the (statetrack, coached) pair down to the (baseline, cot) pair is safe; the order within each pair is not.",
            "Not pure self-play. Unlike Season 2 the prompts genuinely differ, so win rate is informative, but the seats still share one model, so any single result is a team outcome.",
            "Two winners, not one. statetrack and coached separate cleanly from baseline and cot but are level with each other; chain-of-thought, a generic reasoning instruction, matched the control and bought nothing.",
        ],
    }


def build(season_id: str):
    """Return the analysis blob for a season, or None if not available."""
    if season_id == "season-2":
        return _build_season2()
    if season_id == "season-3":
        return _build_season3()
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

    # scatter data: one point per model, for the two-axis plots (Harry's request).
    rat = quant["ratings"]
    scatter_models = [{
        "model": m,
        "wolf": round(rat[m]["wolf"]["rating"]),
        "villager": round(rat[m]["villager"]["rating"]),
        "overall": round(rat[m]["overall"]["rating"]),
        "cost_per_game": cm.get(m, {}).get("cost_per_game"),
        "pareto": cm.get(m, {}).get("pareto_optimal", False),
        "lab": LAB.get(m, ""),
    } for m in order]

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
        "scatter_models": scatter_models,
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
