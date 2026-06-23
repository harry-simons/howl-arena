"""Persist finished games to disk and render readable transcripts.

Two artifacts per game:
1. A canonical JSON record — the lossless "record of truth" (PLAN Validity
   threat 5: replay re-renders the stored record, it does not re-run the seed).
   It captures everything, including each turn's PRIVATE reasoning, so the site
   can later show the gap between what a player said and what it planned.
2. A human-readable .txt transcript — the spectator product in text form,
   pairing every public line with its hidden reasoning.

This is an I/O layer, kept out of the pure engine. The site (Step 6) reads the
JSON directly; scoring (Step 5) can re-aggregate from it offline.
"""

from __future__ import annotations

import dataclasses
import json
from enum import Enum
from pathlib import Path

from engine.types import GameRecord, Phase

DEFAULT_DIR = "games"


# ----- serialization -------------------------------------------------------

def _enum_default(obj):
    if isinstance(obj, Enum):
        return obj.value
    raise TypeError(f"not JSON serializable: {type(obj).__name__}")


def serialize_record(record: GameRecord) -> str:
    """Lossless JSON for a GameRecord (enums become their values)."""
    return json.dumps(
        dataclasses.asdict(record),
        default=_enum_default,
        indent=2,
        ensure_ascii=False,  # keep real UTF-8 (curly quotes, dashes) intact
    )


def _unique_path(path: Path) -> Path:
    """Avoid clobbering an existing file (canonical unique game ids come with
    the Step-5 seating scheduler; until then, never overwrite)."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    n = 2
    while (candidate := path.with_name(f"{stem}-{n}{suffix}")).exists():
        n += 1
    return candidate


def save_record(record: GameRecord, base_dir: str = DEFAULT_DIR) -> Path:
    """Write the canonical JSON record under base_dir/<season>/<game_id>.json."""
    out_dir = Path(base_dir) / (record.season_id or "unsorted")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = _unique_path(out_dir / f"{record.game_id}.json")
    path.write_text(serialize_record(record), encoding="utf-8")
    return path


def save_game(record: GameRecord, base_dir: str = DEFAULT_DIR) -> Path:
    """Write both the JSON record and the readable transcript; return the JSON
    path. The transcript sits beside it with a .txt extension."""
    json_path = save_record(record, base_dir)
    json_path.with_suffix(".txt").write_text(render_transcript(record), encoding="utf-8")
    return json_path


# ----- loading (for offline scoring / analysis) ----------------------------

def load_record(path: str | Path) -> dict:
    """Load a stored record back as a plain dict. Full typed reconstruction is
    deferred until scoring needs it; a dict is enough for the site and re-scoring."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_season(season_id: str, base_dir: str = DEFAULT_DIR) -> list[dict]:
    """All stored records for a season, for offline re-scoring."""
    season_dir = Path(base_dir) / season_id
    return [load_record(p) for p in sorted(season_dir.glob("*.json"))]


# ----- readable transcript -------------------------------------------------

_NIGHT_VERB = {"kill": "targets", "investigate": "investigates", "protect": "protects"}


def _role_of(record: GameRecord, seat_id: int) -> str:
    return record.seat_roles.get(seat_id, "?").upper()


def _reasoning(action) -> str:
    return f"      ↳ thought: {action.private_reasoning}" if action.private_reasoning else ""


def _outcome_events(record: GameRecord, round_number: int, kinds: tuple) -> list[str]:
    return [
        f"  >> {e.detail}"
        for e in record.events
        if e.round_number == round_number and e.kind in kinds
    ]


def render_transcript(record: GameRecord) -> str:
    """The spectator product in text: public message + hidden reasoning, by round."""
    lines: list[str] = []
    res = record.result
    lines.append(f"=== {record.game_id} ===")
    lines.append(f"seed {record.seed}  |  prompt {record.prompt_version}  |  season {record.season_id}")
    if res:
        lines.append(f"WINNER: {res.winner.value} after {res.rounds_played} round(s)")
    lines.append("\nseating (role revealed):")
    for seat_id in sorted(record.seat_models):
        alive = res and seat_id in res.surviving_seat_ids
        lines.append(f"  Player {seat_id}: {record.seat_models[seat_id]:<38} "
                     f"{record.seat_roles.get(seat_id, '?'):<9} {'alive' if alive else 'dead'}")

    rounds = sorted(
        {a.round_number for a in record.actions if a.round_number is not None}
        | {e.round_number for e in record.events}
    )
    for r in rounds:
        ra = [a for a in record.actions if a.round_number == r]

        night = [a for a in ra if a.phase is Phase.NIGHT]
        if night:
            lines.append(f"\n----- Round {r + 1}: NIGHT -----")
            for a in night:
                who = f"Player {a.seat_id} ({_role_of(record, a.seat_id)})"
                if a.outcome.value != "accepted":
                    lines.append(f"  {who}: [{a.outcome.value}]")
                else:
                    verb = _NIGHT_VERB.get(a.action_type.value, a.action_type.value)
                    lines.append(f"  {who} {verb} Player {a.target_seat_id}")
                if reasoning := _reasoning(a):
                    lines.append(reasoning)
            lines += _outcome_events(record, r, ("killed", "no_kill"))

        disc = [a for a in ra if a.phase is Phase.DAY_DISCUSSION]
        if disc:
            lines.append(f"\n----- Round {r + 1}: DISCUSSION -----")
            for a in disc:
                who = f"Player {a.seat_id} ({_role_of(record, a.seat_id)})"
                if a.outcome.value != "accepted":
                    lines.append(f"  {who}: [{a.outcome.value}]")
                else:
                    stance = f" [{a.stance.value}]" if a.stance else ""
                    lines.append(f"  {who}{stance}: {a.message}")
                    if a.lean_target_seat_id is not None:
                        conf = f" ({a.lean_confidence:.0%})" if a.lean_confidence is not None else ""
                        lines.append(f"      ↳ leaning: Player {a.lean_target_seat_id}{conf}")
                if reasoning := _reasoning(a):
                    lines.append(reasoning)

        vote = [a for a in ra if a.phase is Phase.DAY_VOTE]
        if vote:
            lines.append(f"\n----- Round {r + 1}: VOTE -----")
            for a in vote:
                who = f"Player {a.seat_id} ({_role_of(record, a.seat_id)})"
                if a.outcome.value != "accepted":
                    lines.append(f"  {who}: [{a.outcome.value}]")
                else:
                    lines.append(f"  {who} votes for Player {a.target_seat_id}")
                if reasoning := _reasoning(a):
                    lines.append(reasoning)
            lines += _outcome_events(record, r, ("eliminated", "no_elimination"))

    lines += _outcome_events(record, rounds[-1] if rounds else 0, ("game_over",))
    return "\n".join(lines) + "\n"
