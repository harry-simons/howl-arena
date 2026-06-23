"""Build the static site's data artifact from persisted games.

Reads every stored game under games/<season>/, scores each season with the
role-conditional Glicko-2 in scoring.py, builds per-season leaderboards, a
cross-season career layer (stats that do NOT depend on the frozen prompt, so
they ARE comparable across seasons), and a structured replay transcript per
game (public line + private reasoning). Writes site/data/results.js as a
`window.HOWL_DATA = {...}` assignment so the static site reads it with no
server, no fetch/CORS, and no secrets — republishing this file is the whole
update flow (PLAN: publish a new results file, don't redeploy code).

Run:  python export_site.py
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import scoring
import storage
from analysis import site_analysis

SITE_DATA = Path("site") / "data" / "results.js"
# v2: per-model leaderboard rows gained `elo` and `trueskill` rating blocks
# (overall whole-player ladders) alongside the role-conditional Glicko ratings.
SCHEMA_VERSION = 2

# Editorial intro per season (shown on the site). Keyed by season_id.
STORIES = {
    "season-1": (
        "Season 1 — The Featherweights. No frontier titans here: every player is a "
        "small model, 120 billion parameters or fewer, the kind that runs cheap and "
        "fast. Nine of them, drawn from seven labs, dropped into a village where two "
        "are secretly werewolves. A 12-billion-parameter underdog against models many "
        "times its size — and the question was never who is biggest, but who lies best, "
        "and who can smell a lie. Every night the wolves take one of their own; every "
        "day the village argues, accuses, and votes (and, more than once, lynches its "
        "own seer). Deception does not care how many parameters you have. Welcome to "
        "the arena."
    ),
    "season-2": (
        "Season 2 — The Quant Showdown. One model, nine ways. Every player here is "
        "the same brain — gemma-4-31b-it, the model that won Season 1 — but served "
        "at a different precision, from full-resolution bf16 down through fp8 to the "
        "squeezed, compressed fp4 that runs cheapest. Same prompt, same rules, same "
        "model: the only thing that changes between seats is how hard the numbers "
        "have been rounded off. The question is whether you can hear the difference — "
        "does last season's champion, played at fp4, forget who is dead, botch its "
        "votes, and break the format more often than the same model at bf16? Win rate "
        "barely matters when everyone is the same player; watch the quality columns "
        "instead — malformed turns, illegal moves, votes for corpses. Several seats run "
        "the very same endpoint more than once, on purpose: if identical twins score "
        "far apart, that is the noise floor any real difference has to clear. Indicative, "
        "not gospel — nine near-identical players make for subtle differences that "
        "only volume can sharpen. A controlled experiment wearing a werewolf mask."
    ),
}


def _load_all_games() -> list[dict]:
    base = Path(storage.DEFAULT_DIR)
    games = []
    if base.exists():
        for season_dir in sorted(p for p in base.iterdir() if p.is_dir()):
            games.extend(storage.load_record(p) for p in sorted(season_dir.glob("*.json")))
    return games


def _build_transcript(rec: dict) -> dict:
    """Structured, render-ready replay: rounds -> phase turns + outcomes."""
    seat_models = {int(k): v for k, v in rec.get("seat_models", {}).items()}
    seat_roles = {int(k): v for k, v in rec.get("seat_roles", {}).items()}
    actions = rec.get("actions", [])
    events = rec.get("events", [])

    rounds_present = sorted(
        {a.get("round_number") for a in actions if a.get("round_number") is not None}
        | {e.get("round_number") for e in events}
    )

    def turn(a: dict) -> dict:
        return {
            "seat": a.get("seat_id"),
            "model": seat_models.get(a.get("seat_id")),
            "role": seat_roles.get(a.get("seat_id")),
            "type": a.get("action_type"),
            "target": a.get("target_seat_id"),
            "message": a.get("message"),
            "private": a.get("private_reasoning"),
            "stance": a.get("stance"),
            "lean_target": a.get("lean_target_seat_id"),
            "lean_confidence": a.get("lean_confidence"),
            "outcome": a.get("outcome"),
            "note": a.get("note"),
            "provider": a.get("provider"),
        }

    def outcomes(rnd: int, kinds: tuple) -> list[str]:
        return [e["detail"] for e in events
                if e.get("round_number") == rnd and e.get("kind") in kinds]

    rounds = []
    for r in rounds_present:
        ra = [a for a in actions if a.get("round_number") == r]
        rounds.append({
            "number": r + 1,
            "night": [turn(a) for a in ra if a.get("phase") == "night"],
            "discussion": [turn(a) for a in ra if a.get("phase") == "day_discussion"],
            "vote": [turn(a) for a in ra if a.get("phase") == "day_vote"],
            "night_outcome": outcomes(r, ("killed", "no_kill")),
            "vote_outcome": outcomes(r, ("eliminated", "no_elimination")),
        })

    result = rec.get("result") or {}
    return {
        "id": rec.get("game_id"),
        "season": rec.get("season_id"),
        "seed": rec.get("seed"),
        "prompt_version": rec.get("prompt_version"),
        "winner": result.get("winner"),
        "rounds_played": result.get("rounds_played"),
        "seat_models": {str(k): v for k, v in seat_models.items()},
        "seat_roles": {str(k): v for k, v in seat_roles.items()},
        "surviving": result.get("surviving_seat_ids", []),
        "cost": rec.get("cost"),
        "rounds": rounds,
    }


def _career(games: list[dict]) -> list[dict]:
    """Cross-season stats that do NOT depend on the prompt (so comparable)."""
    agg = defaultdict(lambda: {"games": 0, "wins": 0, "wolf_games": 0, "wolf_wins": 0,
                               "village_games": 0, "village_wins": 0, "cost": 0.0,
                               "refused": 0, "actions": 0, "seasons": set()})
    for rec in games:
        if rec.get("void"):
            continue
        winner = (rec.get("result") or {}).get("winner")
        seat_roles = {int(k): v for k, v in rec.get("seat_roles", {}).items()}
        for seat, model in rec.get("seat_models", {}).items():
            role = seat_roles.get(int(seat))
            a = agg[model]
            a["games"] += 1
            a["seasons"].add(rec.get("season_id"))
            wolf = role == scoring.WOLF_ROLE
            won = (winner == "werewolves") if wolf else (winner == "villagers")
            a["wins"] += int(won)
            a[("wolf" if wolf else "village") + "_games"] += 1
            a[("wolf" if wolf else "village") + "_wins"] += int(won)
        for model, c in (rec.get("model_costs") or {}).items():
            agg[model]["cost"] += c.get("total_cost", 0.0)
        for act in rec.get("actions", []):
            m = rec.get("seat_models", {}).get(str(act.get("seat_id"))) or \
                rec.get("seat_models", {}).get(act.get("seat_id"))
            if m:
                agg[m]["actions"] += 1
                if act.get("outcome") == "refused":
                    agg[m]["refused"] += 1

    rows = []
    for model, a in agg.items():
        g = a["games"] or 1
        rows.append({
            "model": model,
            "total_games": a["games"],
            "win_rate": round(a["wins"] / g, 3),
            "wolf_win_rate": round(a["wolf_wins"] / (a["wolf_games"] or 1), 3),
            "village_win_rate": round(a["village_wins"] / (a["village_games"] or 1), 3),
            "refusal_rate": round(a["refused"] / (a["actions"] or 1), 3),
            "cost_total": round(a["cost"], 4),
            "cost_per_game": round(a["cost"] / g, 5),
            "seasons_played": len([s for s in a["seasons"] if s]),
        })
    return sorted(rows, key=lambda r: r["total_games"], reverse=True)


def main() -> None:
    games = _load_all_games()

    by_season = defaultdict(list)
    for rec in games:
        by_season[rec.get("season_id") or "unsorted"].append(rec)

    seasons = []
    leaderboards = {}
    for sid, recs in sorted(by_season.items()):
        scored = scoring.score_games(recs)
        # Rank by overall rating; firm (non-provisional) first.
        board = sorted(
            scored.values(),
            key=lambda r: (not r["overall"]["provisional"], r["overall"]["rating"]),
            reverse=True,
        )
        leaderboards[sid] = board
        prompt_versions = sorted({r.get("prompt_version") for r in recs if r.get("prompt_version")})
        seasons.append({
            "id": sid,
            "prompt_version": prompt_versions[0] if prompt_versions else None,
            "games": len(recs),
            "void_games": sum(1 for r in recs if r.get("void")),
            "models": len(board),
            "story": STORIES.get(sid),
        })

    # Season technical analysis blob (read-ready) per season, if computed.
    # Numbers come from analysis/out/*.json; run the analysis scripts first.
    analysis = {}
    for s in seasons:
        blob = site_analysis.build(s["id"])
        if blob:
            analysis[s["id"]] = blob
            s["has_analysis"] = True

    data = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seasons": seasons,
        "leaderboards": leaderboards,
        "career": _career(games),
        "analysis": analysis,
        "games": [_build_transcript(r) for r in games],
    }

    SITE_DATA.parent.mkdir(parents=True, exist_ok=True)
    payload = "window.HOWL_DATA = " + json.dumps(data, ensure_ascii=False, indent=1) + ";\n"
    SITE_DATA.write_text(payload, encoding="utf-8")
    print(f"Wrote {SITE_DATA}  ({len(games)} games, {len(seasons)} season(s), "
          f"{sum(len(b) for b in leaderboards.values())} model rows)")


if __name__ == "__main__":
    main()
