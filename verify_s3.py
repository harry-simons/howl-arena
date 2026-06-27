"""Verify the Season 3 prompt variants — offline build-check, then live probe.

Two stages:

  OFFLINE (always runs, no key, no spend): for every seat, build the real chat
  messages for a sample turn and assert the variant's system prompt is what we
  expect — baseline is the frozen prompt verbatim; each treatment is the baseline
  plus exactly its one appended block; the game-state user message is byte-for-
  byte identical across all variants (the controlled-experiment invariant). Also
  confirms the nine seat labels are unique.

  LIVE (only if OPENROUTER_API_KEY is set): send one cheap real turn per DISTINCT
  variant through the shared Novita bf16 route and confirm it routes there and
  returns a parseable action — the S1/S2 pin-check, adapted. Skipped cleanly with
  a message if no key is present.

Run:  python -u verify_s3.py        (offline only, unless the key is exported)
"""

from __future__ import annotations

import os
import sys

from adapter import parsing, prompt
from adapter.config import AdapterConfig
from adapter.openrouter import OpenRouterClient
from engine.game import GameConfig, GameState
from engine.types import Phase, Role
from season3_config import (S3_ROUTE, SEATS, VARIANTS, _COACHED_EXTRA,
                            _COT_EXTRA, _STATETRACK_EXTRA)

_EXPECTED_EXTRA = {
    "baseline": None,
    "coached": _COACHED_EXTRA,
    "cot": _COT_EXTRA,
    "statetrack": _STATETRACK_EXTRA,
}


def _sample_view():
    """A real villager day-vote view to build prompts against."""
    state = GameState("verify", seed=5, config=GameConfig(9, 2, num_seers=1, num_healers=1))
    state.assign_roles([f"m{i}" for i in range(9)])
    state.phase = Phase.DAY_VOTE
    seat = next(s for s, p in state.players.items() if p.role is Role.VILLAGER)
    return state.build_player_view(seat)


def verify_offline() -> None:
    print("OFFLINE — variant construction\n", flush=True)
    view = _sample_view()
    base_user = prompt.build_user(view)

    for key, extra in _EXPECTED_EXTRA.items():
        v = VARIANTS[key]
        want_system = prompt.BASE_SYSTEM if extra is None else prompt.BASE_SYSTEM + "\n\n" + extra
        msgs = v.build_messages(view)
        assert msgs[0]["role"] == "system" and msgs[0]["content"] == want_system, \
            f"{key}: system prompt is not what was expected"
        # The user (game-state) message must be identical across every variant.
        assert msgs[1]["content"] == base_user, \
            f"{key}: user message diverged from baseline — not a controlled comparison"
        if extra is None:
            assert v.system == prompt.BASE_SYSTEM, "baseline must be the frozen prompt verbatim"
            note = "frozen baseline verbatim"
        else:
            assert v.system.startswith(prompt.BASE_SYSTEM + "\n\n"), \
                f"{key}: treatment is not baseline + appended block"
            note = f"baseline + {len(extra)} chars of guidance"
        print(f"  OK {v.version:<14} {note}", flush=True)

    labels = [s.label for s in SEATS]
    assert len(set(labels)) == len(labels) == 9, "seat labels must be unique and number 9"
    assert all(s.routing == S3_ROUTE for s in SEATS), "every seat must share the one route"
    print(f"\n  OK 9 unique seats, all on one route {S3_ROUTE['order'][0]} "
          f"{S3_ROUTE['quantizations'][0]}:", flush=True)
    for s in SEATS:
        print(f"       {s.label:<26} [{s.variant.version}]", flush=True)
    print("\nOffline checks passed.\n", flush=True)


def verify_live() -> None:
    try:
        config = AdapterConfig.from_env()
    except RuntimeError as exc:
        print(f"LIVE — skipped (no key): {exc}")
        return

    print("LIVE — one real turn per distinct variant via the shared route\n", flush=True)
    transport = OpenRouterClient(api_key=config.api_key, base_url=config.base_url,
                                 timeout=config.timeout, max_retries=1)
    view = _sample_view()
    want = S3_ROUTE["order"][0]
    ok = 0
    for key, v in VARIANTS.items():
        try:
            msgs = v.build_messages(view)
            r = transport.complete(model_id=SEATS[0].api_model_id, messages=msgs,
                                   temperature=0.0, max_tokens=400,
                                   provider_override=S3_ROUTE)
            served = r.provider or "(no provider field)"
            parsed = parsing.parse_reply(r.text, view)
            routed = "OK " if served == want else "?? "
            verdict = "parsed" if parsed.action is not None else f"UNPARSED ({parsed.error})"
            ok += int(served == want and parsed.action is not None)
            print(f"  {routed}{v.version:<14} -> {served:<10} {r.latency_s:5.1f}s  {verdict}",
                  flush=True)
        except Exception as exc:
            print(f"  XX {v.version:<14} -> FAILED: {type(exc).__name__}: {str(exc)[:80]}",
                  flush=True)

    print(f"\n{ok}/{len(VARIANTS)} variants routed to {want} and parsed cleanly.", flush=True)
    if ok < len(VARIANTS):
        print("A variant that failed to route or parse needs a look before the season runs.",
              flush=True)


if __name__ == "__main__":
    verify_offline()
    if "--offline" not in sys.argv:
        verify_live()
