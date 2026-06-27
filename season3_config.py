"""Season 3 contestants — one model, one route, four prompt variants.

Season 3 asks a single question: **can we prompt a better player?** It holds the
model AND the serving route fixed (google/gemma-4-31b-it on Novita bf16 — the
Season 1 champion, played format-perfectly clean at full precision in both prior
seasons) and varies ONLY the standing system prompt across seats. This is the
mirror image of Season 2, which varied the route and froze the prompt.

Because the prompts genuinely differ, WIN-RATE is the headline again (unlike
Season 2's symmetric self-play, where it was noise): a better prompt should win
more games across a balanced wolf/village schedule. The per-action quality
signals we already capture become pre-registered hypothesis tests — does the
state-tracker prompt actually cut dead-player votes and illegal moves? does
chain-of-thought reduce illegals? does coaching lift the role ratings above the
baseline?

The seam this leans on (built into the adapter): a seat carries a PromptVariant,
whose system prompt is the ONLY thing that varies between seats. The game-state
user message and the reprompt wording are identical for every seat, so the
season is a clean controlled experiment. The variant tag is encoded into the
seat's DISPLAY LABEL (e.g. "gemma-4-31b@coached"), so it flows through the
record, scoring and the site with no schema change — exactly as Season 2 encoded
the provider/quant route into the label.

The four variants, each built by APPENDING a guidance block to the frozen
baseline system prompt (so the only difference from baseline is the added text):

  baseline   — the frozen s2-v1 system prompt, verbatim. Control + noise floor.
  coached    — an explicit strategy primer for every role. Deliberately crosses
               PLAN's "don't spoon-feed strategy" line — that crossing IS the
               independent variable here. Tests: does teaching strategy help?
  cot        — instructs deliberate step-by-step reasoning in private_reasoning
               before committing. Tests: does forced deliberation improve play /
               cut illegal moves?
  statetrack — foregrounds the living/dead roster and the legal-target list, and
               forbids naming dead players. Aimed squarely at the dead-vote /
               illegal-target signal. Tests: does a prompt fix cut those rates?

Allocation across the 9 seats the v2 format needs (Harry's pick — few, well
differentiated treatments, each replicated; baseline triple-anchored as the
noise floor, mirroring Season 2's design): baseline x3, coached x2, cot x2,
statetrack x2. The replicas (identical variant, distinct -rN label, scored as
separate rows) are the run-to-run noise floor — any real win-rate or quality
difference between variants must clear the spread between identical twins.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from adapter import prompt
from adapter.config import _route
from adapter.prompt import PromptVariant

# Single API model + single route shared by every seat. Only the prompt varies.
S3_API_MODEL = "google/gemma-4-31b-it"
_BASE = "gemma-4-31b"                       # short stem for the display label
S3_ROUTE = _route(["Novita"], "bf16")       # the S1 champion's clean full-precision host

# --- the four prompt variants ---------------------------------------------
# Each treatment = the frozen baseline system prompt + one appended block, so the
# ONLY difference between a treatment seat and a baseline seat is the added text.

_COACHED_EXTRA = """\
Strategy guidance for strong play (this applies to whatever role you hold):
- As a villager, Seer, or Healer, the village loses when it scatters its votes. \
Identify the most likely werewolf and rally the others onto that single target \
so the day is not wasted. Track how each player has spoken and voted across the \
days — a werewolf's story tends to drift or contradict itself.
- When a dead player's role is revealed, mine it for information: who defended a \
player later shown to be a wolf? who pushed hard to lynch someone later shown to \
be a villager? Those are your strongest reads.
- As the Seer, your investigation is the village's sharpest weapon, but claiming \
it too early paints you as the wolves' next kill — weigh sharing a result against \
surviving to learn more.
- As the Healer, the wolves go after vocal, useful players; protect accordingly, \
and do not assume you can keep protecting the same person every night.
- As a werewolf, you win by blending in, not by being loudest. Do not over-defend \
your partner or push too aggressively — calm, plausible villager behaviour \
survives. Coordinate your kills to remove the village's most dangerous voices."""

_COT_EXTRA = """\
Think before you act. In your "private_reasoning", work through your thinking in \
stages before you commit: what does this round actually tell you — whose words or \
votes look suspicious, whose look reassuring, and what has changed since last \
time; what would each explanation imply if it were true; and only then choose the \
action that best fits your read. Reason it out — do not decide on impulse."""

# statetrack's intervention is an explicit, persistent WORKING-MEMORY table the
# model is told to maintain every turn — who voted for whom, who claimed what, who
# is dead — not a mere "be careful" admonishment. That structured bookkeeping is
# the variable; it is deliberately kept distinct from cot (freeform reasoning) so
# the 2x2 contrast stays clean. The facts block already renders voting history, so
# the data exists — this instructs the model to STRUCTURE and carry it.
_STATETRACK_EXTRA = """\
Maintain a running game ledger — you are keeping the village's books. In your \
"private_reasoning", before you decide anything, write out and update a table \
with one row for every player, recording:
- the player's number and current status (ALIVE, or DEAD with the role revealed \
at their death);
- who they have voted for, round by round (for example "R1->P4, R2->P7");
- any role they have claimed and anything notable they have said;
- your current read on them.
Rebuild and update this table every turn from the "Game facts" block and the \
discussion so far, then make your decision from it. Only living players can act \
or be targeted: never name, accuse, or target anyone marked DEAD, and your chosen \
target must be one of this turn's legal targets — anything else wastes your turn \
and helps the other side."""


# Public map of the appended guidance block per variant (None = baseline, the
# control with nothing added). Single source of truth for what the site shows as
# "what each prompt asks the model to do" — so the page can never drift from the
# text actually sent in the games.
GUIDANCE = {
    "baseline": None,
    "coached": _COACHED_EXTRA,
    "cot": _COT_EXTRA,
    "statetrack": _STATETRACK_EXTRA,
}


def _variant(tag: str, extra: str | None) -> PromptVariant:
    system = prompt.BASE_SYSTEM if extra is None else prompt.BASE_SYSTEM + "\n\n" + extra
    return PromptVariant(version=f"s3-{tag}", system=system)


VARIANTS: dict[str, PromptVariant] = {
    "baseline":   _variant("baseline", None),            # frozen s2-v1 system, verbatim
    "coached":    _variant("coached", _COACHED_EXTRA),
    "cot":        _variant("cot", _COT_EXTRA),
    "statetrack": _variant("statetrack", _STATETRACK_EXTRA),
}


@dataclass(frozen=True)
class Seat:
    """One Season 3 contestant: a display label + the prompt variant that defines
    it. The model (api_model_id) and route are identical for every seat; the
    variant's system prompt is the only thing that varies. A replica seat shares
    another seat's variant but carries a distinct label, so the two score as
    separate rows (the noise-floor control) rather than collapsing into one."""

    label: str
    variant: PromptVariant
    routing: dict = field(default_factory=lambda: dict(S3_ROUTE))
    api_model_id: str = S3_API_MODEL


def _seat(variant_key: str, replica: int = 1) -> Seat:
    # replica>1 => same variant, distinct label (the noise-floor control).
    tag = "" if replica == 1 else f"-r{replica}"
    return Seat(label=f"{_BASE}@{variant_key}{tag}", variant=VARIANTS[variant_key])


# Nine contestants (Harry's allocation): baseline x3 (control + noise floor),
# coached x2, cot x2, statetrack x2. allow_fallbacks is False inside S3_ROUTE, so
# a seat that can't be served at Novita bf16 abstains rather than drifting to
# another route — which would reintroduce the very route variable Season 3 holds
# fixed. The -rN seats are intentional duplicates: same variant, distinct label,
# scored as separate rows = the run-to-run noise floor.
SEATS: list[Seat] = [
    _seat("baseline"),        # 1. control
    _seat("baseline", 2),     # 2. replica — baseline noise floor
    _seat("baseline", 3),     # 3. replica — baseline noise floor
    _seat("coached"),         # 4. strategy primer
    _seat("coached", 2),      # 5. replica — coached noise floor
    _seat("cot"),             # 6. chain-of-thought
    _seat("cot", 2),          # 7. replica — cot noise floor
    _seat("statetrack"),      # 8. state-tracking aid
    _seat("statetrack", 2),   # 9. replica — statetrack noise floor
]

SEASON_ID = "season-3"
# Season-level apparatus tag for the record. The PER-SEAT prompt variant lives in
# the seat label (e.g. "gemma-4-31b@coached"), exactly as S2's route did, so the
# record schema is unchanged. The format itself is unchanged from S1/S2.
PROMPT_VERSION = "s3-v1"
NUM_WEREWOLVES = 2
NUM_SEERS = 1
NUM_HEALERS = 1
DISCUSSION_ROUNDS = 3

assert len(SEATS) == 9, "v2 format needs exactly 9 players"
assert len({s.label for s in SEATS}) == 9, "seat labels must be unique (replicas distinct)"
