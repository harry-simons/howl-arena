"""Season 2 contestants — one model, nine provider/quant routes.

Season 2 is a controlled QUANTIZATION / PROVIDER study (not a multi-model
ladder). ONE model — google/gemma-4-31b-it, the Season 1 champion — is seated
nine ways across the precision range it is served at, bf16 -> fp8 -> fp4, so the
only thing that varies between seats is serving quality. Hypothesis: worse
quants play worse (more malformed / illegal / dead-vote turns, lower role
ratings).

Why gemma and not the gpt-oss example: gemma won Season 1 and — more to the
point for THIS experiment — played essentially perfectly clean on format at full
precision (0 malformed / 988 actions on Novita bf16), so there is maximal
headroom for low quants to visibly degrade it. (gpt-oss-120b was also clean but
finished last on skill; gemma gives the cleaner "the champion gets compressed"
contrast.)

The seam this leans on (built into the adapter): a seat's DISPLAY LABEL (its
identity in the record, scoring and on the site) is decoupled from the API
model id and from a per-seat routing block. All nine seats send the same
api_model_id; each carries its own provider+quant route.

Endpoint selection (live-verified 2026-06-22 via verify_s2_routes.py, NOT the
catalogue — the /endpoints listing falsely shows Chutes fp4 as status-OK but it
404s "no endpoints found" under allow_fallbacks:false, so it is dropped). gemma
has no fp16 endpoint (bf16 is its full precision). After live shake-out only
TWO hosts proved both reliable AND fast: Novita (bf16, ~4s/call) and DeepInfra
(fp8 + fp4, ~4s/call, and takes concurrent load with zero 429s). Dropped:
Venice (flaky, 26s on a probe), Chutes (fp4 404s despite the catalogue),
SiliconFlow fp8 (degraded), WandB (rate-limited — 429s after ~13 calls), and
Parasail (reliable but ~28s/call, 7x slower; its only unique value was a thin
n=1 same-quant cross-provider fp8 check, not worth dragging every game).

To reach the nine the v2 format needs, the field is filled with REPLICAS of the
reliable hosts, giving a balanced 3/3/3 ladder (bf16 x3 / fp8 x3 / fp4 x3) with
a replica pair at EVERY precision level. Replicas (identical route, distinct -rN
label, scored as separate rows) are a deliberate NOISE-FLOOR control: identical
twins should score alike, so their spread measures the intrinsic run-to-run
variance — the error bar any real cross-quant difference must clear. Having one
at each level also answers a question of its own: does that variance GROW as the
quant drops? Bonus from the fp4 constraint: DeepInfra serves bf16-adjacent fp8
AND fp4, so DeepInfra-fp8 vs DeepInfra-fp4 isolates the quant effect on ONE host
with zero provider confound — the cleanest comparison in the study.
"""

from __future__ import annotations

from dataclasses import dataclass

from adapter.config import _route

# Single API model shared by every seat. Identity/route are what vary.
S2_API_MODEL = "google/gemma-4-31b-it"
_BASE = "gemma-4-31b"  # short stem for the display label


@dataclass(frozen=True)
class Seat:
    """One Season 2 contestant: a display label + the route that defines it.

    label is the identity (e.g. "gemma-4-31b@Novita-bf16") carried through the
    GameRecord, the scorer and the leaderboard. api_model_id + routing are how
    the call is actually made. A replica seat shares another seat's route but
    carries a distinct label, so the two score as separate rows (the noise-floor
    control) rather than collapsing into one.
    """

    label: str
    quant: str
    routing: dict
    api_model_id: str = S2_API_MODEL


def _seat(provider: str, quant: str, replica: int = 1) -> Seat:
    # replica>1 => identical route, distinct label (the noise-floor control).
    tag = "" if replica == 1 else f"-r{replica}"
    return Seat(label=f"{_BASE}@{provider}-{quant}{tag}", quant=quant,
                routing=_route([provider], quant))


# Nine contestants, high precision -> low (live-verified hosts; confirmed with
# Harry). gemma tops out at bf16 (no fp16 endpoint exists). allow_fallbacks is
# False inside _route, so a seat that can't be served at its exact provider+quant
# abstains rather than silently drifting to another route — which would
# contaminate the very variable we are measuring. The -rN seats are intentional
# duplicates (see module docstring): same route as their base, distinct label,
# scored as separate rows = the noise-floor control.
SEATS: list[Seat] = [
    _seat("Novita", "bf16"),       # 1. full-precision; the S1 champion's host
    _seat("Novita", "bf16", 2),    # 2. replica of #1 — bf16 noise floor
    _seat("Novita", "bf16", 3),    # 3. replica of #1 — bf16 noise floor
    _seat("DeepInfra", "fp8"),     # 4. the mid step
    _seat("DeepInfra", "fp8", 2),  # 5. replica of #4 — fp8 noise floor
    _seat("DeepInfra", "fp8", 3),  # 6. replica of #4 — fp8 noise floor
    _seat("DeepInfra", "fp4"),     # 7. the degradation zone
    _seat("DeepInfra", "fp4", 2),  # 8. replica of #7 — fp4 noise floor
    _seat("DeepInfra", "fp4", 3),  # 9. replica of #7 — fp4 noise floor
]
# Hosts: just TWO, both fast (~4s/call) and verified clean under concurrency —
# Novita (bf16 x3) and DeepInfra (fp8 x3 + fp4 x3). Dropped hosts: WandB (429s),
# Parasail (~28s/call, 7x slower — and its sole value, a same-quant cross-provider
# fp8 comparison, was a thin n=1 control not worth the latency). This is now a
# clean WITHIN-host quant study: DeepInfra bf16-adjacent fp8 vs fp4 isolates the
# quant effect with zero provider confound, Novita anchors full precision, and a
# replica pair at every level gives the run-to-run noise floor.

SEASON_ID = "season-2"
# Format is UNCHANGED from S1, so the prompt version is unchanged too (s2-v1).
# S2 is a different experiment, not a different game — same apparatus.
NUM_WEREWOLVES = 2
NUM_SEERS = 1
NUM_HEALERS = 1
DISCUSSION_ROUNDS = 3

assert len(SEATS) == 9, "v2 format needs exactly 9 players"
assert len({s.label for s in SEATS}) == 9, "seat labels must be unique (replicas distinct)"
