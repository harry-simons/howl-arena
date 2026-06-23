"""Runtime configuration for the model adapter.

Holds the knobs that shape a model call. The API key is read from the
environment and never stored in code (the site is read-only and the repo holds
no secrets). The model id is intentionally a placeholder default here: which
cheap models to seat is decided later, so it is set per run via the
OPENROUTER_MODEL environment variable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# OpenRouter is OpenAI-compatible; all provider quirks stay behind this base url.
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

# Placeholder. Real model selection is deferred (see PLAN Step 2/3). Override per
# run with OPENROUTER_MODEL rather than editing this file.
DEFAULT_MODEL_ID = "openrouter/auto"

# Sampling temperature for play. Real models at temperature > 0 are not
# deterministic; that is expected (Validity threat 5 — replay re-renders the
# stored record, it does not re-run the seed).
#
# TECH DEBT: temperature is an apparatus variable but GameRecord has no field to
# store it. For true cross-game rigour it should be persisted on the record.
# Deferred until the record schema is revisited; flagged here so it is not lost.
DEFAULT_TEMPERATURE = 0.8

# Cap on output tokens per call. Raised to 2000 so reasoning models (qwen3-32b,
# glm-4.5-air) can think AND still emit the closing JSON — at 800 they burned the
# budget mid-thought and got truncated into malformed replies.
DEFAULT_MAX_TOKENS = 2000

# Network timeout per call, in seconds. Dropped 60->45: a hung upstream was
# stalling whole games (no per-game timeout yet). 45s still clears reasoning
# calls (~25-40s) but fails a truly stuck provider fast so the game proceeds.
DEFAULT_TIMEOUT = 45.0


def _route(providers, quant: str = None) -> dict:
    """OpenRouter routing restricted to a curated SET of providers (load-balanced
    across them, no fallback beyond the set) and, when given, a single advertised
    quantization. Multiple providers spread concurrent load so no one upstream is
    hammered (the Groq 429/timeout storm); the quant filter keeps serving
    consistent so a routing/quant drift can't confound a model's measured skill.
    Single-element lists = a hard pin. Providers chosen to share one advertised
    quant; fp4 and known-bad/slow (Baidu, DekaLLM) excluded."""
    # `order` + allow_fallbacks:false restricts to exactly this set and fails
    # over WITHIN it (e.g. if the first is rate-limited) — that failover is the
    # load-spread under concurrency. (`only` is NOT a valid OpenRouter key.)
    r = {"order": list(providers), "allow_fallbacks": False}
    if quant:
        r["quantizations"] = [quant]
    return r


# Per-model routing: spread across same-quant providers where they exist, so
# concurrency doesn't pin one host. Groq removed entirely (its 429s + the
# malformed/timeout storm broke the first 10-game run).
PROVIDER_PREFS: dict[str, dict] = {
    "openai/gpt-oss-120b": _route(["Cerebras"], "fp16"),                # only fp16 host; fast+clean
    "meta-llama/llama-3.3-70b-instruct": _route(["SambaNova", "Novita"], "bf16"),
    "qwen/qwen3-next-80b-a3b-instruct": _route(["Alibaba"]),            # full quant (unadvertised), clean
    "qwen/qwen3-32b": _route(["DeepInfra", "Nebius", "AtlasCloud", "SiliconFlow"], "fp8"),  # OFF Groq; 4-way
    "openai/gpt-oss-20b": _route(["DeepInfra"], "bf16"),               # SiliconFlow was 22s; DeepInfra fast+bf16
    "google/gemma-4-31b-it": _route(["Novita", "Venice"], "bf16"),
    "z-ai/glm-4.5-air": _route(["Novita"], "bf16"),                    # only bf16 host; token-cap fix handles malformed
    "mistralai/mistral-nemo": _route(["DeepInfra", "Novita"], "fp8"),
    "amazon/nova-lite-v1": _route(["Amazon Bedrock"]),                 # only endpoint
}


@dataclass
class AdapterConfig:
    """Everything one model call needs that is not derived from the view."""

    model_id: str = DEFAULT_MODEL_ID
    temperature: float = DEFAULT_TEMPERATURE
    max_tokens: int = DEFAULT_MAX_TOKENS
    base_url: str = DEFAULT_BASE_URL
    timeout: float = DEFAULT_TIMEOUT
    api_key: str = ""

    @classmethod
    def from_env(cls) -> "AdapterConfig":
        """Build config from environment variables.

        Raises if the API key is absent so a real run fails loudly rather than
        silently calling with no auth.
        """
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set. Export it before running a real "
                "game; it must never be committed to the repo."
            )
        return cls(
            model_id=os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL_ID),
            api_key=api_key,
        )
