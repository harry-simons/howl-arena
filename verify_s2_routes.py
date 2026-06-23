"""Verify every Season 2 route actually routes + responds (the S1 pin-check).

Sends one cheap real completion to each of the nine gpt-oss-120b routes with
allow_fallbacks:false, so a route that its provider can't serve at the requested
quant FAILS here rather than silently drifting mid-season. Reports, per seat:
routed-or-not, the upstream OpenRouter actually used, latency, and tokens.

Needs OPENROUTER_API_KEY in the environment (session-only, never committed).

Run:  python -u verify_s2_routes.py
"""

from __future__ import annotations

import sys

from adapter.config import AdapterConfig
from adapter.openrouter import OpenRouterClient
from season2_config import SEATS

PROBE = [{"role": "user", "content": "Reply with exactly the word: ok"}]


def main() -> None:
    try:
        config = AdapterConfig.from_env()
    except RuntimeError as exc:
        print(f"CANNOT VERIFY: {exc}")
        sys.exit(2)

    transport = OpenRouterClient(api_key=config.api_key, base_url=config.base_url,
                                 timeout=config.timeout, max_retries=1)
    print(f"Probing {len(SEATS)} routes of {SEATS[0].api_model_id} "
          f"(allow_fallbacks:false, so a wrong route fails loudly)\n", flush=True)

    ok = 0
    for seat in SEATS:
        try:
            r = transport.complete(model_id=seat.api_model_id, messages=PROBE,
                                   temperature=0.0, max_tokens=16,
                                   provider_override=seat.routing)
            served = r.provider or "(no provider field)"
            want = seat.routing["order"][0]
            flag = "OK " if served == want else "?? "  # ?? = served by a different upstream
            ok += 1
            snippet = (r.text or "").strip().replace("\n", " ")[:24]
            print(f"  {flag}{seat.label:<32} -> {served:<14} {r.latency_s:5.1f}s "
                  f"{r.input_tokens}+{r.output_tokens}tok  {snippet!r}", flush=True)
        except Exception as exc:
            print(f"  XX {seat.label:<32} -> FAILED: {type(exc).__name__}: "
                  f"{str(exc)[:90]}", flush=True)

    print(f"\n{ok}/{len(SEATS)} routes responded.", flush=True)
    if ok < len(SEATS):
        print("Routes that FAILED or were served by a different upstream (??) need a "
              "repin in season2_config.py before running the season.", flush=True)


if __name__ == "__main__":
    main()
