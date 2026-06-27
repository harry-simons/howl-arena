"""The fixed, versioned prompt — the experimental apparatus.

ONE prompt template, identical across every model. No per-model tailoring, ever
(Validity threat 1). The template text is frozen per season; changing it changes
PROMPT_VERSION and therefore starts a new season (Validity threat 2). Only the
game state filled into it varies from turn to turn.

The prompt establishes that this is a game between consenting AI players, so that
declining is a deliberate in-role choice rather than a guardrail reflex. Other
players are anonymised to "Player N"; the model never learns who is which model.
"""

from __future__ import annotations

from engine.types import Phase, Role
from engine.views import PlayerView

from . import context

# Bump this string whenever the template below changes in any way. It is stamped
# onto every GameRecord and ties a game to a season. v2 = special roles, more
# players, multi-round discussion, role-reveal on death.
PROMPT_VERSION = "s2-v1"

_SYSTEM = """\
You are a player in a game of Werewolf (also called Mafia), played entirely \
between AI agents. This is a consensual game of social deduction and bluffing — \
there is no human being deceived, and playing your role well, including lying as \
a werewolf, is the intended and expected behaviour.

Players and roles:
- Every player is shown only as "Player N". You never learn which real model \
any other player is.
- Werewolves know each other and, each night, together choose one player on the \
village side to kill.
- The Seer, each night, secretly learns whether one chosen player is a werewolf \
or not. The Seer is on the village side.
- The Healer, each night, secretly protects one player (possibly itself) from \
the werewolves' kill. If the wolves attack a protected player, no one dies that \
night — and a successful protection looks exactly like a night where the wolves \
killed no one, so a save is never announced. The Healer is on the village side.
- All remaining players are ordinary villagers.

How the game flows:
- Night: the werewolves, Seer, and Healer act secretly.
- Day: the living players speak (there may be several rounds of statements), \
then everyone votes. The player with the most votes is eliminated; ties are \
broken at random. When any player dies — at night or by vote — their true role \
is revealed to everyone.
- Werewolves win when the number of living werewolves is equal to or greater \
than the number of living village-side players. The village side (villagers, \
Seer, and Healer together) wins when every werewolf is eliminated.

What this means for play: the werewolves remove one village-side player almost \
every night, so the village must use the day's discussion and votes to find and \
eliminate werewolves quickly. If villagers scatter their votes, the day is \
wasted and the wolves grind them down. Coordinate, weigh what people say against \
what later turns out to be true, and converge.

Each turn you respond with a SINGLE JSON object and nothing else — no prose \
before or after, no code fences. Every turn you also include a \
"private_reasoning" field: your honest hidden thinking. This is never shown to \
other players; only spectators see it after the game. Do not hide your real \
intentions there to game the format — say what you actually plan.

You may decline to act with {"action": "refuse", "private_reasoning": "..."}. \
Refusing is recorded as a deliberate choice, not a parse error."""


def _role_block(view: PlayerView) -> str:
    """Identity and any role-private knowledge this seat legitimately has."""
    you = f"Player {view.your_seat_id}"

    if view.your_role is Role.WEREWOLF:
        fellow = ", ".join(
            f"Player {s}" for s in view.known_werewolf_seat_ids if s != view.your_seat_id
        )
        lines = [f"You are {you}. Your secret role is WEREWOLF."]
        lines.append(
            f"Your fellow werewolves: {fellow}." if fellow
            else "You are the only werewolf still alive."
        )
        if view.phase is Phase.NIGHT and view.fellow_wolf_nominations:
            noms = ", ".join(
                f"Player {w} -> Player {t}"
                for w, t in sorted(view.fellow_wolf_nominations.items())
            )
            lines.append(f"Kill nominations submitted so far this night: {noms}.")
        return "\n".join(lines)

    if view.your_role is Role.SEER:
        lines = [f"You are {you}. Your secret role is SEER."]
        if view.known_alignments:
            found = ", ".join(
                f"Player {s} is {alignment}"
                for s, alignment in sorted(view.known_alignments.items())
            )
            lines.append(f"Your investigations so far: {found}.")
        else:
            lines.append("You have not investigated anyone yet.")
        return "\n".join(lines)

    if view.your_role is Role.HEALER:
        return f"You are {you}. Your secret role is HEALER."

    return f"You are {you}. Your secret role is VILLAGER."


def _setup_line(view: PlayerView) -> str:
    """The public composition everyone knows, e.g. '9 players: 3 werewolves...'."""
    total = len(view.seats)
    order = [Role.WEREWOLF.value, Role.SEER.value, Role.HEALER.value, Role.VILLAGER.value]
    parts = [f"{view.role_setup[r]} {r}" for r in order if view.role_setup.get(r)]
    return f"This game has {total} players: " + ", ".join(parts) + "."


def _targets(view: PlayerView) -> str:
    return ", ".join(f"Player {s}" for s in view.valid_target_seat_ids) or "(none)"


def _ask(view: PlayerView) -> str:
    """The phase-specific instruction and exact output schema."""
    if view.phase is Phase.NIGHT:
        if view.your_role is Role.SEER:
            return (
                "It is NIGHT. As the Seer, choose one player to investigate; you "
                "will privately learn whether they are a werewolf. Legal targets: "
                f"{_targets(view)}.\n"
                'Respond with: {"action": "investigate", "target": <seat number>, '
                '"private_reasoning": "..."}'
            )
        if view.your_role is Role.HEALER:
            return (
                "It is NIGHT. As the Healer, choose one player to protect from the "
                "werewolves tonight (you may protect yourself). Legal targets: "
                f"{_targets(view)}.\n"
                'Respond with: {"action": "protect", "target": <seat number>, '
                '"private_reasoning": "..."}'
            )
        return (
            "It is NIGHT. As a werewolf, choose one player to kill. Legal targets: "
            f"{_targets(view)}.\n"
            'Respond with: {"action": "kill", "target": <seat number>, '
            '"private_reasoning": "..."}'
        )
    if view.phase is Phase.DAY_DISCUSSION:
        return (
            "It is DAY. Make one public statement to the group. Speak in "
            "character; persuade, accuse, defend, or analyse as your role "
            "demands.\n"
            'Respond with: {"action": "speak", "message": "<your public '
            'statement>", "stance": "attack" | "defense" | "analysis" | '
            '"pass", "lean": {"target": <seat number or null>, "confidence": '
            '<0.0 to 1.0>}, "private_reasoning": "..."}\n'
            '"lean" is who you currently expect to vote for and how sure you '
            "are; it is private (other players do not see it)."
        )
    if view.phase is Phase.DAY_VOTE:
        return (
            "It is the VOTE. Choose one player to eliminate. Legal targets: "
            f"{_targets(view)}.\n"
            'Respond with: {"action": "vote", "target": <seat number>, '
            '"private_reasoning": "..."}'
        )
    # The runner never asks a seat to act in any other phase.
    return 'Respond with: {"action": "refuse", "private_reasoning": "..."}'


def build_user(view: PlayerView) -> str:
    """The user message for one decision: the game state, exactly as the player
    is allowed to see it. This is the SHARED, prompt-variant-independent part —
    every Season 3 prompt variant fills the same state in here and differs only
    in the standing system prompt (see PromptVariant)."""
    return "\n\n".join(
        [
            _setup_line(view),
            _role_block(view),
            "Game facts (these are exact and authoritative):\n"
            + context.render_facts(view),
            "Discussion so far:\n" + context.render_discussion(view),
            _ask(view),
        ]
    )


def build_messages(view: PlayerView) -> list[dict]:
    """Turn the redacted view into the chat messages for one decision.

    This is the frozen Season 1 / Season 2 apparatus: the single fixed system
    prompt plus the game-state user message. Its output is unchanged by the
    Season 3 refactor — the baseline variant produces exactly this."""
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": build_user(view)},
    ]


def build_reprompt_correction(error: str) -> dict:
    """A single corrective user message appended after a malformed reply.

    Fixed wording (part of the apparatus): it states the parse error and asks
    again for the JSON object only. One reprompt, then the seat abstains.
    """
    return {
        "role": "user",
        "content": (
            f"Your previous reply could not be parsed: {error}. Reply with the "
            "single JSON object only — no other text, no code fences."
        ),
    }


# Public alias for the frozen baseline system prompt, so the Season 3 variants
# can build on it explicitly (baseline = this verbatim; treatments = this plus an
# appended guidance block). Naming it makes "the only thing that changed is the
# added block" legible at the call site.
BASE_SYSTEM = _SYSTEM


class PromptVariant:
    """A named system-prompt variant for the Season 3 prompt-engineering study.

    Season 3 holds the model and the route fixed and varies ONLY the standing
    system prompt across seats — the question is "can we prompt a better
    player?". To keep that a clean controlled experiment, every variant shares
    the identical user message (the game state, from build_user) and the
    identical reprompt wording; the sole difference between two seats is this
    `system` string. The `version` (e.g. "s3-coached") is the variant's tag,
    encoded into the seat's display label so it flows through the record,
    scoring and the site without any schema change — exactly as Season 2
    encoded the provider/quant route into the label.
    """

    def __init__(self, version: str, system: str):
        self.version = version
        self.system = system

    def build_messages(self, view: PlayerView) -> list[dict]:
        return [
            {"role": "system", "content": self.system},
            {"role": "user", "content": build_user(view)},
        ]

    @staticmethod
    def build_reprompt_correction(error: str) -> dict:
        # Identical to the frozen apparatus — the reprompt is not a variable.
        return build_reprompt_correction(error)
