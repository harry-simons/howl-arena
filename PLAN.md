# Howl Arena — LLM Werewolf Benchmark — Build Plan & Handoff

A spectator-friendly AI benchmark where frontier models play social-deduction
Werewolf (a.k.a. Mafia) against each other. Output is a public leaderboard with
three scores per model and replayable game transcripts.

Project name: **Howl Arena** (domain target: howlarena.com, fallback howlarena.gg).

This file is the single source of truth for picking the work back up in Claude
Code. It records what exists, why it is built this way, and what to build next.

## The idea in one line

Multiple LLMs play Werewolf head to head; deception and deduction skill is
ranked via role-conditional Elo, and the game transcripts are the entertainment.

## Why this benchmark

- **Spectator appeal:** deception is inherently watchable; transcripts are the product.
- **Ranking cleanliness:** role-symmetric play over many seeded games averages out luck.
- **Capability gap:** no mainstream benchmark cleanly separates deception from
  deception-detection. This one does, by scoring the two roles separately.

## Scoring model (decided)

Three scores, not one:

- **Werewolf score** — Elo updated only for seats that played wolf; win = wolves won.
- **Villager score** — Elo updated only for seats that played villager; win = villagers won.
- **Overall** — a weighted blend of the two (start 50/50), NOT an independent
  ladder, so it cannot drift from the role scores.

Rationale: a model can be a strong liar and a weak detective or vice versa.
Separating the roles also fixes refusal entanglement: a model that only refuses
the wolf role keeps a clean villager score.

Use **Glicko-2** rather than plain Elo so each rating carries an uncertainty
band. A leaderboard that says "these two are within noise" is more defensible
and more interesting than false precision.

### Ranking-cleanliness controls (must hold or the ranking is luck)

1. **Role-balanced seating:** over a season every model sits as wolf and villager
   a roughly equal number of times, against a balanced mix of opponents.
2. **Volume:** single games are coin-flips. Plan for hundreds of games per
   season. Cheap because it is all text.
3. **Report confidence, not just rank:** show rating intervals, not bare order.

Brutal fact to keep stating publicly: a single wolf's outcome depends heavily on
its fellow wolf and on villager sharpness, so these scores are noisier than a
chess Elo no matter what. Do not oversell single-game results.

## Architecture (layers)

1. **Game engine** — pure, deterministic, no model calls, no I/O. The trustworthy core.
2. **Player adapter** — the ONLY model-aware layer. Translates engine state to a
   prompt and parses replies back into validated actions. Normalises across
   providers so no single API quirk leaks into the engine.
3. **Match runner** — loops the engine through phases, calls adapters, records everything.
4. **Scoring / ladder** — consumes finished-game records, updates ratings.
   Separate from the runner so historical games can be re-scored without re-running.
5. **Public site** — leaderboard (three columns + uncertainty) and replayable transcripts.

The critical seam: agents implement `get_action(view: PlayerView) -> Action`.
Dummies and real models share that interface, so the runner never changes when
models replace dummies.

## What exists now (Step 1 + wolf coordination) — COMPLETE

```
werewolf/
  engine/
    __init__.py
    types.py     # data structures only: Role, Phase, Team, Action, events, records
    views.py     # PlayerView: the redacted, per-seat view (information boundary)
    game.py      # GameState + GameConfig: the deterministic rules engine
    runner.py    # ScriptedAgent (dummy) + MatchRunner + PlayerAgent protocol
  tests/
    smoke_test.py  # 5 checks, all passing
  PLAN.md
```

Run the tests: `python -m tests.smoke_test` from the `werewolf/` directory.

### Verified properties (the 5 checks)

1. A full 7-player, 2-wolf game runs to a valid win condition.
2. Determinism: replays from the same seed are byte-identical.
3. Information boundary: villagers cannot see wolf identities; wolves can.
4. Refusal and malformed output are recorded as distinct outcomes in the audit trail.
5. Wolf coordination: wolves converge on one target by plurality, no silent overwrite.

### Rules implemented (v1, deliberately minimal)

- 7 players, 2 werewolves (configurable via `GameConfig`, validated so wolves
  start below parity).
- Night: each wolf nominates; later wolves see earlier nominations via the
  wolf-only `fellow_wolf_nominations` field; kill resolved by plurality, ties
  broken by seeded rng.
- Day discussion: one message per alive player, in seat order.
- Day vote: one vote per alive player; plurality eliminated; ties broken by
  seeded rng; zero votes eliminates no one.
- Win: wolves win at parity (wolves >= villagers); villagers win when all wolves dead.
- No special roles (no seer/doctor) in v1, by choice. They multiply prompt
  complexity and illegal-action surface without improving ranking cleanliness.

### Decisions already locked

- **Random tie-break stays** (both night and day), seeded for determinism.
- **Wolf coordination is single-round** (a wolf sees nominations submitted before
  it, not after). Enough for v1. Multi-round private wolf negotiation is a clean
  future extension: the nomination store and wolf-only view field already exist.
- **Overall score = blend of role scores**, not a third ladder.

### Refusal handling (built into the engine, not bolted on)

Every action passes through one validator. A refusal is just another outcome:

- Wolf refuses to kill -> no kill that night, logged as `REFUSED` against that
  model in the wolf role.
- Villager refuses to vote -> abstention, logged.
- Malformed/illegal output -> `MALFORMED` / `ILLEGAL`, distinct from `REFUSED`
  because they mean different things about the model.

The scoring consequence is automatic (a wolf that will not kill almost always
loses). The refusal log feeds a public "won't play" stat — the humorous jab —
and should be reported separately from skill so a selectively-abstaining model
is not unfairly ranked as merely bad.

## Build order (remaining)

- [x] **Step 1 — Game engine + scripted dummies.** DONE.
- [x] **Wolf coordination fix.** DONE.
- [ ] **Step 2 — Model adapter (one real model).** Implement `PlayerAgent`
      against one provider (likely OpenRouter). Build the prompt from
      `PlayerView`, parse the reply into a validated `Action`, map declines to
      `REFUSED` and unparseable output to `MALFORMED`. Add a single reprompt on
      malformed before abstaining. Get ONE real game completing. **Instrument
      cost-per-game from this first real game** (see Cost section): capture token
      usage and price from the response metadata per call and total it onto the
      `GameRecord`.
- [ ] **Step 3 — Generalise to 3–4 models.** Confirm provider-neutral. No single
      API quirk in the engine or runner.
- [ ] **Step 4 — Runner hardening.** Timeouts, retries, concurrency limits, and
      per-game cost logging. (Runner already has a never-crash abstention
      fallback.) Cost per game becomes a first-class logged metric here, with a
      per-game and per-season budget cap that aborts cleanly if exceeded.
- [ ] **Step 5 — Persistence + scoring.** Serialise `GameRecord` to storage.
      Implement Glicko-2 over stored records (re-scorable offline). Season-level
      role-balanced seating scheduler.
- [ ] **Step 6 — Public site.** Leaderboard (3 scores + intervals + refusal stat)
      first; replayable transcripts second. Transcripts are the spectator draw,
      so design the read model for readability from day one. Surface
      cost-per-game and cost-per-model as a stat (it is genuinely interesting:
      cheap models that punch above their price are a story in themselves).

## Inspiration: foaster.ai Werewolf benchmark (what to take, where to go further)

Reference: werewolf.foaster.ai (a prior LLM-Werewolf benchmark with role-conditioned
Elo). It is well executed but a static one-off; our edge is going further and
keeping it live. Take the good ideas, fix the ceilings.

### What they do well — adopt these

- **Day/night visual modes** in the replay UI. Strong, atmospheric, cheap to do.
- **Per-model character profiles.** Each model gets a named archetype, a
  signature tactic, and a failure mode (e.g. "audacious impersonator", "brittle
  theorist", "social chameleon"). This is the single most shareable, memorable
  feature. Generate these from observed play, not hand-written.
- **Public message + private reasoning, shown side by side.** THE key feature.
  Each public utterance is paired with the model's private thought, so the
  spectator sees the gap between the story told and the plan executed. This is
  where the entertainment lives and our engine does not capture it yet (see
  engine change below).
- **Vote-intention tracking before and after each message.** Lets you show who
  swung whom — persuasion made visible. Capture stated vote intention pre- and
  post- each day message.
- **Stance-tagged speech** (attack / defend / analyse) with a speaking order that
  prioritises defence then attack then analysis. Adds structure and readable
  signal to discussion.
- **Mayor mechanic** elected before Night 1 with daytime tie-break power, to fix
  the flat early game. Optional for us (see below).
- **Named "strategic moments"** pulled out as highlights with context, the move,
  the private calculation, and the impact.

### Where they stopped short — our differentiation

- **Static, not live.** Their board is a single round-robin, already going stale.
  Ours updates over a season as new games and models land. This is the core edge.
- **No spectator interactivity.** Theirs is a read-only essay. We can let viewers
  guess the wolf before the reveal, or vote on the most convincing liar, turning
  passive reading into participation (still within the read-only-site constraint:
  guesses are client-side, no secrets).
- **Manipulation metrics buried in prose.** They computed auto-sabotage and
  manipulation-success but only narrate them. We surface them as live, sortable
  leaderboard stats (see metrics below).
- **No cost/efficiency angle.** Nobody shows which cheap model punches above its
  price. We already have cost-per-game; make "value for money" a headline stat.

### Engine changes this implies (do in Step 2, before scaling)

1. **Capture private reasoning alongside every action.** Extend `Action` (or the
   adapter output) with an optional `private_reasoning` field. The model produces
   a public message AND a private thought each turn; the engine stores both. The
   private thought is NEVER shown to other players (information boundary), only
   to spectators in the replay. This is the marquee feature, so build the data
   path for it now even if the UI comes later.
2. **Capture vote intention per discussion message.** Let a day-discussion turn
   optionally carry a current lean (target + confidence). Store the sequence so
   the replay can show vote swings across rounds.
3. **Stance tag on day speech** (attack / defend / analyse / pass). Cheap to add
   to the `SPEAK` action; drives both readable replays and a speaking-order rule.

### Metrics to compute from stored games (Step 5) and surface (Step 6)

These come straight from the audit trail, no extra model calls:

- **Manipulation success** (wolf): share of day phases where, while the model is
  a wolf, the village eliminates a villager rather than a wolf. Split D1 vs D2 —
  sustaining it into D2 is the real signal, since claims and night outcomes
  should erode a wolf's story by then.
- **Auto-sabotage** (villager): share of games where the villager side eliminates
  its own power role (if/when special roles are added). Proxy for suggestibility.
- **Day-1 coordination detection** (villager): rate of spotting paired pushes and
  bloc votes. Proxy for calibrated skepticism.
- **Vote-swing / persuasion**: net vote intentions a model moves per message.

### Deferred decision: special roles and mayor

foaster uses Seer + Witch + an elected Mayor. Our v1 deliberately has none (see
Rules implemented). Their experience is evidence the mayor genuinely fixes a flat
early game, so it is the most justified first addition AFTER the base ladder
works. Hold special roles until the three-score ladder is stable; add the mayor
first if early games look flat, since it generates information (candidacies,
justifications, vote patterns) from minute one. Each role added multiplies prompt
complexity and illegal-action surface, so add deliberately, one at a time.



A hard separation between where games run and where results are shown:

- **Games run locally, on Harry's machine,** via API (OpenRouter). All model
  calls, all API keys, all cost, all orchestration live here. Nothing about
  running a game ever touches the public site.
- **The website is read-only.** It hosts and displays results only: the
  leaderboard (3 scores + intervals + refusal stat + cost stat) and replayable
  transcripts. It never calls a model, never runs a game, never holds an API key.
- **No secrets in the website, ever.** No API keys, no provider credentials, no
  `.env` committed, nothing in client-side code or the repo. The site is a static
  reader over a published results file.
- **Update flow:** local runs produce `GameRecord` data -> exported to a static
  results artifact (e.g. a JSON file or small static dataset) -> the site reads
  that artifact and renders it. Updating the leaderboard means publishing a new
  results file, not deploying code or exposing any key.

Implication for the build: the site is a static frontend (no server-side
secrets, no backend that calls providers). A static host is sufficient. Keep the
results schema stable and versioned so the site and the local runner can evolve
independently.



```python
# The seam every agent implements (engine/runner.py):
class PlayerAgent(Protocol):
    model_id: str
    def get_action(self, view: PlayerView) -> Action: ...

# What the agent receives (engine/views.py) — already redacted per seat:
PlayerView(
    your_seat_id, your_role, round_number, phase, seats, public_log,
    known_werewolf_seat_ids,        # wolves only
    fellow_wolf_nominations,        # wolves only, at night
    valid_target_seat_ids,          # legal targets for the current request
)

# What the agent returns (engine/types.py):
Action(seat_id, action_type, target_seat_id=None, message=None,
       outcome=ActionOutcome.ACCEPTED, note=None)
# action_type in {KILL, SPEAK, VOTE, ABSTAIN}
# set outcome=REFUSED when the model declines in role
# set outcome=MALFORMED when the reply cannot be parsed
```

The engine validates everything the adapter returns, so the adapter does not
need to police legality — it only needs to map model output to an `Action` and
flag refusals/parse failures honestly.

## Adapter design notes (Step 2, before coding)

- **Discussion format:** v1 is one message per player per day round. Cleanest to
  parse and score. Free-flowing multi-turn chat is more watchable but harder to
  bound and rank; hold it for v2 once the ladder works.
- **Prompt must establish it is a game** with consenting AI players, not real
  deception of a human. This clears most refusals before any penalty machinery
  matters. Do this first.
- **Parse strictly, fail honestly.** Prefer a constrained output format (e.g. a
  small JSON object: `{"action": "...", "target": N, "message": "..."}`),
  reprompt once on parse failure with the error, then abstain as `MALFORMED`.
- **Anonymise model identity to other players.** Players see "Player 3", never
  the model name, so reputation cannot leak into play. Model->seat mapping lives
  only in the record for scoring.
- **Provider neutrality:** keep all provider-specific request shaping inside the
  adapter. The engine and runner must stay model-agnostic.

### Context construction (Step 2) — facts deterministic, narrative summarised

To control cost (the transcript-capping lever in the Cost section below) the
adapter does NOT send the full transcript on every call. It sends a bounded
context built from two distinct sources:

1. **Structured game facts — rendered from engine events, never summarised.**
   The engine already logs every vote as a `PublicEvent` with `seat_id`,
   `target_seat_id` and round number, plus deaths and eliminations. A small
   deterministic helper turns these into a compact, always-correct block injected
   verbatim into every prompt. This MUST include the **voting history (who voted
   for whom, per round)**, which is the single most important thing to preserve,
   plus who is alive/dead and how each death happened. Do NOT let a model
   re-derive these from prose: a summariser can get them wrong, and then the
   context holds a model's faulty recollection instead of ground truth.

2. **Discussion narrative — LLM-summarised, cheap and swappable.** Free-text
   argument is what is expensive to carry in full and where lossy compression is
   acceptable. A summariser model condenses the discussion (accusations,
   defences, claims) into a few lines. This is the ONLY part an LLM summarises.

   - **Make the summariser model a config variable, decided later.** Default to a
     cheap model (e.g. DeepSeek / a Flash-class model); keep it swappable so it
     can be tuned without touching the engine or adapter logic.
   - Because the summariser never sources any mechanical fact (votes, deaths,
     alive/dead), it can be as cheap and fallible as you like: if it garbles the
     discussion flavour, the game still plays correctly.

Design rule to hold: **anything that affects play legality or scoring comes from
engine events; only the rhetorical colour comes from the summariser.**



Per-game cost is a first-class metric, not an afterthought. The driver is that
every model is called many times per game and each call carries the growing
transcript as input context.

### Rough token shape (7 players, 2 wolves)

- A game runs ~2 to 4 day rounds before a win.
- Per round: up to 7 speak calls + 7 vote calls, plus ~2 wolf night calls.
- Total ~40 to 70 model calls per game.
- Transcript grows, so later calls carry more input. Mid-game input ~1k to 3k
  tokens/call; outputs small (~50 to 200 tokens: a sentence, or a structured vote).
- Per game, summed across all seats: order of **100k to 250k input tokens** and
  **5k to 15k output tokens**.

### Cost bands (entirely dependent on the models seated)

- **All cheap models** (~sub-$0.20/M input): **~£0.005 to a few pence** per game.
- **Mixed mid-tier** (~$0.50 to $3/M): **~5 to 30 pence** per game.
- **All frontier** (~$3 to $15+/M, more on output): **~£0.40 to £2+** per game.

The real budget lever is **volume, not per-game cost**. Ranking needs hundreds of
games per season to tighten intervals. At ~200 games: cheap models cost pennies
total; an all-frontier ladder could run **£100 to £400+**.

### Levers if cost bites

1. **Cap the transcript sent.** Send only the current round plus a short running
   summary instead of the full log. Stops input tokens ballooning in long games.
   This is the single biggest lever and should be designed into the adapter
   (Step 2), not retrofitted.
2. **Tier the volume.** Run the bulk of games on cheaper models; reserve
   frontier-vs-frontier for a smaller, higher-signal set.
3. **Per-game and per-season budget caps** in the runner (Step 4) that abort
   cleanly when exceeded.

### Instrumentation

OpenRouter returns token usage and price in the response metadata. Capture it
per call, total it onto the `GameRecord` (add a `cost` field: input tokens,
output tokens, currency, total), and you get a real number after the first dozen
games rather than trusting estimates. Estimates above are order-of-magnitude
only; replace them with measured figures as soon as Step 2 runs.

### Running it cheaply / free-tier and credit strategy

Season 1 is ~100 games on cheap models, which permanent free tiers cover
outright. No loopholes needed (and gaming free tiers risks bans for no real
gain). Verify live quotas before relying on numbers — free tiers are changing
fast (Google cut theirs in late 2025).

Recommended zero-cost stack for season 1 (gives the 3–4 distinct models the
benchmark needs):

- **Google AI Studio — Gemini Flash / Flash-Lite.** The only top-tier permanent
  free API tier; no card. ~1,500 req/day at ~10 RPM = roughly 20–30 full games/day
  free. Confirm current limits at ai.google.dev.
- **Groq** free tier for fast short calls.
- **OpenRouter free models** (Mistral, Gemma, Llama variants) behind one
  OpenAI-compatible key — also the planned production endpoint.

Credits worth applying for in parallel (low effort, legitimate):

- **OpenRouter startup deal:** ~$1,000 universal inference credits, 6 months,
  across 300+ models incl. Anthropic/OpenAI/Google, plus 0% processing fees ~12
  months. Pairs directly with the planned OpenRouter stack; eligibility reviewed.
- **Claude for Open Source** (launched Feb 2026): qualifying OSS maintainers get
  6 months Claude Max 20x free (~$1,200). A real reason to open-source the Howl
  Arena engine — clean codebase, good fit.
- **Anthropic Startup Program:** direct, rolling, generally easier than OpenAI's
  gated routes for a bootstrapped project.
- **Google for Startups Cloud (AI-first tier, up to $350K):** out of scope for a
  fun solo project now (needs AI/ML core + traction/accelerator signal), but
  noted if this ever grows legs.

Strategy: do NOT chase credits for season 1 — run free tiers. Apply for the
OpenRouter deal and Claude for Open Source in parallel as upside.



- **Overall blend weight:** 50/50 default. If detection is the more interesting
  capability, weight villager higher. Decide deliberately — it sets what the
  leaderboard rewards.
- **Refusal reporting:** rank on all games, or only games where all seats played?
  Changes the data model. Leaning toward reporting refusal as a separate public
  stat and keeping role-Elo on played seats.
- **Season structure:** games per model, how seating balance is enforced, when
  ratings reset.

## Season sizing and the all-seasons site (decided)

### How many games per season

Assumes 7 models, 7 seats, 2 wolves. The 5:2 seat split means each game yields
5 villager data points but only 2 wolf, so **wolf scores are always the binding
constraint** — villager counts run far ahead of wolf for the same game total.
Glicko-2 rating deviation (RD) is the real arbiter; these are rules of thumb for
a fun board:

- **Indicative (~50 games):** ~14 wolf / ~36 villager appearances each. Soft launch.
- **Credible (~100 games):** ~28 wolf / ~72 villager each. **Season 1 target.**
- **Confident (~175 games):** ~50 wolf each. Diminishing returns for a fun project.

A role score is noise under ~15 games, indicative at 15–30, solid at 50+. Mark a
model "provisional" until its RD drops below a chosen cutoff, then publish its rank.

### Adding models in later seasons

The 8th model is introduced at the **start of season 2** (not mid-season). With
8 models and 7 seats, **one model sits out each game** — this is fine; Glicko is
relative and only needs the interaction graph to stay connected. To hold the same
per-model confidence, budget ~15% more games per added model:

- **Season 1 (7 models): ~100 games.**
- **Season 2 (8 models): ~115–120 games.**
- Scale ~+15% per further model added.

Rotation rules when models > seats: cycle which model sits out, randomise the
other seats, and keep opponents well-mixed so no model's rating reflects only
easy/hard opposition. A newly added model starts at default rating with high RD,
provisional until ~20–25 games in. (This extends the Step 5 seating scheduler.)

### Leaderboard is all-seasons; ranking is per-season

The site is a single permanent home for every season. Ranking is filtered to a
season because **ratings are only comparable within a season** (prompt is frozen
per season; a prompt change starts a new season — see Validity threats 1–2). Do
NOT compute a single cross-season Elo: it would average across different
experimental conditions and mean nothing.

Two layers:

- **Per-season ranking.** Default view = current season; filter to any past
  season. Each season has its own clean ladder with RD/uncertainty.
- **Cross-season career layer (honest across seasons).** Stats that do NOT depend
  on the frozen prompt and so ARE comparable: total games, lifetime win rate,
  lifetime wolf vs villager win rate, refusal-rate trend, cost efficiency, and a
  **"seasons won" tally**. The seasons-won count does the "best of all time" job
  honestly and is more fun than a fake blended rating.

Design rule: season is a filter on one permanent site, not a wall between sites.
Never publish a single number claiming all-time best skill.

## Special roles — unranked variants (down the road, design now)

Decided framing so it does not get rebuilt wrong later. Season 1 has NO special
roles (see Rules implemented). When added (season 2+), they are **unranked
variants layered on the existing three scores, not a fourth ladder**:

- A Healer/Seer/Witch is mechanically still a **villager** for win-condition and
  scoring. A model in that seat feeds its **villager score** as normal. No new
  rank is created.
- The genuinely interesting question — *does model X in the Healer seat raise the
  village win rate?* — is a **role-effectiveness stat**, NOT a per-model rank:
  "village win rate when model X holds role R" vs baseline. It needs its own
  balanced sampling (every model needs enough games in role R to compare), which
  is why it is a season 3+ analysis once volume exists.
- **Add order by complexity:** the **mayor first** — a pure social mechanic
  (elected, daytime tie-break), no new night action, lowest illegal-action
  surface, and the prior art found it genuinely fixes a flat early game. Hold
  Seer/Healer/Witch until you specifically want to study power-role effectiveness;
  each adds a validated night action and its own prompt slice.

Caution: every special role multiplies prompt complexity and the ways a model can
emit an illegal action. Add one at a time, deliberately.

## Animations (site polish — Step 6, optional but on-brand)

Scoped to what Howl Arena needs: a read-only leaderboard and a replay viewer.
Animation must communicate, not decorate. Data animations must stay honest (a
bar filling to its value is fine; a bar overshooting and settling back implies
motion that is not real — never do that). Save personality for the chrome (mood,
reveals, microinteractions), keep the numbers literal.

Ready-to-use prompts for Claude Design / the site build:

1. **Day/night transition (signature).** Smoothly shift the replay viewer between
   a light "day" palette and a dark "night" palette as the game changes phase.
   Background colour, not a literal sun/moon. ~600ms crossfade, room-changing-mood
   feel, not a jarring theme switch. Day warm/bright, night cool/dim.
2. **Vote-flow on elimination.** As each day vote resolves, draw a thin line/arrow
   from voter to target, then fade and desaturate the eliminated row. Stagger
   votes ~150ms each so the pile-on builds before elimination lands.
3. **Night kill reveal.** Reveal the killed player quietly: row dims, a small
   "killed in the night" label fades in. Understated and a little ominous, no
   gore, just a player going quiet.
4. **Role reveal at game end.** Card-flip each player to reveal hidden role
   (werewolf/villager), staggered across players, werewolves on a distinct accent.
   This is the payoff moment; let it feel like a reveal.
5. **Public message vs private thought (marquee).** When a spectator expands a
   turn, slide the private reasoning out from behind the public message like
   pulling back a curtain. Public line stays put; private thought eases in.
   The contrast of "what they said" vs "what they thought" should feel like a
   small unmasking.
6. **Leaderboard rank change.** On re-sort or new results, animate rows reordering
   with a smooth FLIP-style position transition (~400ms) so a model visibly climbs
   or drops rather than the table snapping. Core spectator hook of a live board.
7. **Rating / uncertainty band.** Animate each rating bar filling from zero to its
   value on load, uncertainty band easing in just after, staggered top to bottom.
   Makes the "within noise" overlaps visible as bands settle.
8. **Refusal-rate microinteraction (humour).** On hover over the refusal-rate
   stat, a tiny wry beat — e.g. a small wolf icon that turns away / covers its
   eyes. Subtle and dry, one beat, not a cartoon.

**If only two are built:** (1) day/night, because it is the atmospheric signature
and aids orientation in a replay, and (6) rank-change reorder, because watching a
model climb is the core draw of a live leaderboard. The rest are nice-to-have.

NOTE: an Anthropic "animations" feature was referenced but not confirmed at time
of writing; these are described so they translate to Claude Design, artifacts, or
hand-built CSS/JS regardless of the exact tool.


These are the ways the benchmark could silently measure the wrong thing. The
first two are existential: get them wrong and you are ranking your own prompt,
not the models. Treat this section as the experimental-rigour checklist.

### 1. Prompt quality IS the benchmark (highest priority)

Everything rides on how a model is asked to play. A weak prompt makes a strong
model look stupid, so without discipline here you rank prompt engineering, not
models. The earlier inspiration write-up showed models behaving completely
differently on framing alone.

- **One fixed, identical prompt across all models.** No per-model tailoring,
  ever, even to "help" a weaker model parse the format. Any per-model tweak
  destroys comparability.
- **The prompt is the experimental apparatus,** not a Step 2 implementation
  detail. Design it as carefully as the scoring.
- **Freeze it before a season and never touch it mid-season.**

### 2. Prompt versioning and cross-season comparability

The moment the prompt changes, ratings before and after are NOT comparable.

- **Version the prompt and tie the version to each season.** Store the prompt
  version on every `GameRecord`.
- **A prompt change starts a fresh season.** Do not mix games from different
  prompt versions into one ladder, or the leaderboard silently blends
  incomparable numbers.

### 3. Statistical honesty — how many games, and "tied" is a valid answer

"Hundreds of games" is directionally right but must be made concrete.

- **Minimum games before a model gets a published rating.** Decide the threshold
  (Glicko-2's rating deviation gives a principled cutoff); below it, show
  "provisional" not a rank.
- **Show ties honestly.** When intervals overlap, the honest output is "these six
  are within noise", not a forced 1-2-3-4-5-6. Commit to rendering overlapping
  bands as tied rather than inventing precision. This is more defensible AND more
  interesting than false ranking.

### 4. The summariser is a silent confound

Every model reads the discussion summary, so the summariser's quality affects
every model's play. Engine facts are protected (see Context construction), but
the *persuasion* signal flows through the summary, so a garbled accusation looks
like a model failure when it is a summariser failure.

- **Prefer NO summariser in v1.** While games are short, send the full discussion
  transcript. Only introduce summarisation when context genuinely overflows.
- If/when summarisation is needed, treat the summariser version as another
  apparatus variable (version it, freeze per season) and watch for it distorting
  persuasion outcomes.

### 5. Determinism vs. real models — replay means replay the record

The engine is deterministic from a seed; real models at temperature > 0 are not.

- **Stored `GameRecord`s are the record of truth.** "Replay" = re-render the
  stored game, NOT re-run the seed.
- Re-running a seed with a real model will diverge. That is expected, not a bug.
  Do not conflate engine determinism with reproducible model behaviour.

### 6. Anti-degeneracy — void games vs. scored games

Models can collapse into pass-loops, all-abstain rounds, or stalemates that are
not refusals exactly, just nothing happening. `max_rounds` stops infinite games
but a game where everyone passes to the cap is garbage, not a villager win.

- **Define a void-game rule.** E.g. if a day produces no substantive speech and
  no elimination for N consecutive rounds, or the game hits `max_rounds` without
  a real win condition, mark it VOID and exclude from scoring.
- Log void games separately; a model that frequently induces voids is itself a
  signal worth showing.

### 7. Order effects and positional bias

The same research lineage (foaster's bias study) found models biased by player
name/position. We anonymise to "Player N", which helps, but we currently always
act in seat order, so position can still leak into the ranking.

- **Randomise speaking and night-action order per round** (seeded, so still
  replayable), or at minimum log order as a variable so bias can be checked.
- Keep seat-to-model assignment randomised per game (already done at role
  dealing; extend the principle to turn order).

### 8. Operational reality of a long season

Running hundreds of games hits rate limits, provider outages, and — very real on
OpenRouter — models being deprecated mid-season.

- **Decide the roster policy:** freeze the model roster at season start, or
  tolerate mid-season additions/removals. Freezing is cleaner for comparability.
- **Handle a deprecated model:** define what happens to its rating and to games
  in flight (e.g. mark its rating final-as-of-deprecation, do not delete its
  historical games).
- **Resumability:** the runner must checkpoint so a crashed or rate-limited season
  resumes without re-running completed games (ties to Step 5 persistence).

### 9. Format-gaming and boundary checks

Models may try to exploit the format rather than play.

- **Validate the information boundary holds in practice,** not just in theory: a
  model must not smuggle its vote into `private_reasoning` to hide it from
  opponents while still affecting the game, pad messages to manipulate the
  summariser, or infer and address seats it should not know.
- Add a lightweight validation pass over recorded games that flags suspicious
  patterns (e.g. private_reasoning containing the literal vote when the public
  message does not). Minor, but cheap insurance.

### Priority order

Threats 1 and 2 are existential — they decide whether this is a real benchmark or
an elaborate prompt-ranking exercise. Resolve them before the first real season.
3 through 9 are quality issues to fix as they surface, but designing for them now
(especially 6 void-games and 8 resumability) is far cheaper than retrofitting.

