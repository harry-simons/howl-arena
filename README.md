# Howl Arena — a benchmark for liars

Howl Arena is a social-deduction benchmark for language models. Models play **Werewolf**: most are villagers trying to find the wolves, two are secretly werewolves trying to survive. To win, a wolf has to lie convincingly; a villager has to catch a lie. The benchmark measures both.

**▶ Live site:** _(GitHub Pages link added on publish)_

## What it measures

Each model plays many games, drawing different roles, and is scored on how well it plays each side:

- **Role-conditional Glicko-2** — separate Werewolf and Villager skill ratings (plus a 50/50 overall), with rating-deviation bands so you can see what's signal and what's noise.
- **Elo** and **TrueSkill** — two whole-player ladders, each game scored as a real two-team match (winners beat losers) as alternative lenses on the same results.
- **Behavioural metrics** — refusal rate, illegal/malformed actions, dead-vote rate, self-inflicted partner leaks (a wolf outing its ally), day-1 lynch accuracy, vote accuracy, and cost per game.

The site also renders **full replays** — every public message alongside the private reasoning the model never showed the table, so you can see the gap between what a player said and what it was thinking.

## Season 1 — The Featherweights

Nine small models (120 billion parameters or fewer) from seven labs, 108 games, in a nine-player village with two wolves and two power roles (a seer who can investigate, a healer who can protect). The question was never who is biggest, but who lies best and who can smell a lie.

## How it works

```
engine/        # the rules — turn order, roles, win conditions, never-crash runner
adapter/       # model interface — prompt (frozen, versioned), context builder, strict JSON parser, OpenRouter transport
scoring.py     # Glicko-2 (role-conditional) + Elo + TrueSkill, all computed offline from the game records
analysis/      # per-season deep analysis (significance, deception, power roles, cost, profiles)
export_site.py # reads games/<season>/*.json, scores them, writes the static site data
site/          # the full reader (all seasons, career view)
publish/       # the trimmed public build (single season) served by GitHub Pages
games/         # persisted game records, one JSON + human-readable .txt per game
tests/         # offline tests — engine smoke test and adapter tests, zero API spend
```

The whole site is static: `export_site.py` writes `window.HOWL_DATA = {...}` into a single JS file, and `index.html` reads it. No server, no database, no fetch — republish to update.

## Reproducing

Models are reached through [OpenRouter](https://openrouter.ai), so the key never lives in the repo:

```bash
export OPENROUTER_API_KEY=...        # environment only — never committed

python -m tests.smoke_test           # engine sanity, no API calls
python -m tests.adapter_test         # adapter sanity against a fake transport, no API calls

python run_games.py <count> <seed> <concurrency>   # play and persist games
python export_site.py                              # score everything and rebuild the site
```

Then open `site/index.html` (or `publish/index.html`) directly in a browser.

## Design notes

- **One frozen, versioned prompt** is used for every model in a season — no per-model prompt tuning. Ratings are comparable only within a season because the prompt is frozen per season.
- The engine **never crashes on a bad model turn**: refusals, malformed JSON, and illegal moves are recorded as distinct outcomes rather than aborting the game, so model failure modes become data.
- Ratings shown as **provisional** until a model has enough games for a firm rank.

## Status

Season 1 is complete and published. Season 2 (frontier-scale routing experiments) is in progress and excluded from this repo until it's ready.
