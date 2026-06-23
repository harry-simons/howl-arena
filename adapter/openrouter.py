"""OpenRouter transport and per-call cost capture.

The transport is deliberately thin: it sends an OpenAI-compatible chat request
and returns the text plus token/price usage. Provider-specific request shaping
lives here and nowhere else. Tests use a fake transport that implements the same
Transport protocol, so the prompt/parse/reprompt logic is exercised offline with
no API spend.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional, Protocol

from engine.types import GameCost


@dataclass
class CallResult:
    """One model call's output and its measured cost.

    cost is taken from OpenRouter's response usage when present. It may be None
    if the provider did not return a price for the call; tokens are still
    captured so cost can be reconstructed later from published rates.
    """

    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost: Optional[float] = None
    currency: str = "USD"
    latency_s: float = 0.0   # wall-clock seconds for the call (transport-measured)
    # Upstream provider OpenRouter routed this call to (e.g. "DeepInfra"), the
    # exact served model string, and the quantization we ENFORCED for it (the
    # response carries no quant, but our routing filter guarantees it). Lets us
    # attribute quality/latency to a provider AND a precision level.
    provider: Optional[str] = None
    served_model: Optional[str] = None
    quant: Optional[str] = None


@dataclass
class ModelStats:
    """Per-model totals, so latency and spend can be compared across models."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    latency_s: float = 0.0

    @property
    def avg_latency_s(self) -> float:
        return self.latency_s / self.calls if self.calls else 0.0


class Transport(Protocol):
    """The single capability the agent depends on. Real and fake share this."""

    def complete(
        self,
        model_id: str,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
        provider_override: Optional[dict] = None,
    ) -> CallResult:
        ...


class CostAccumulator:
    """Totals cost across every call in a game.

    One accumulator is shared by all seats so the whole-game figure can be
    attached to the GameRecord. Cost-per-game is a first-class metric (see PLAN
    Cost section), not an afterthought.
    """

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_cost = 0.0
        self.currency = "USD"
        self.calls = 0
        # Per-model breakdown (latency, tokens, cost) for comparing models.
        self.per_model: dict[str, ModelStats] = defaultdict(ModelStats)
        # Per-provider breakdown — which OpenRouter upstream served the call.
        self.per_provider: dict[str, ModelStats] = defaultdict(ModelStats)
        # True once any call reported a price; if no call ever did, total_cost is
        # not trustworthy and the record should say so rather than imply "free".
        self._saw_priced_call = False

    def add(self, result: CallResult, model_id: Optional[str] = None) -> None:
        self.input_tokens += result.input_tokens
        self.output_tokens += result.output_tokens
        self.calls += 1
        if result.cost is not None:
            self.total_cost += result.cost
            self.currency = result.currency
            self._saw_priced_call = True
        if model_id is not None:
            self._accumulate(self.per_model[model_id], result)
        if result.provider:
            self._accumulate(self.per_provider[result.provider], result)

    @staticmethod
    def _accumulate(stats: "ModelStats", result: CallResult) -> None:
        stats.calls += 1
        stats.input_tokens += result.input_tokens
        stats.output_tokens += result.output_tokens
        stats.cost += result.cost or 0.0
        stats.latency_s += result.latency_s

    def to_game_cost(self) -> GameCost:
        return GameCost(
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            total_cost=self.total_cost if self._saw_priced_call else 0.0,
            currency=self.currency,
            calls=self.calls,
        )

    def model_game_costs(self) -> dict[str, GameCost]:
        """Per-model cost, for recording on the GameRecord (cost-per-model stat)."""
        return {
            model: GameCost(
                input_tokens=s.input_tokens,
                output_tokens=s.output_tokens,
                total_cost=s.cost,
                currency=self.currency,
                calls=s.calls,
            )
            for model, s in self.per_model.items()
        }

    @property
    def price_observed(self) -> bool:
        """Whether any call returned a price. False means total_cost is unknown,
        not zero — surfaces an instrumentation gap instead of hiding it."""
        return self._saw_priced_call


class OpenRouterClient:
    """Real transport over OpenRouter's chat-completions endpoint."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        timeout: float,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        provider_prefs: Optional[dict] = None,
    ):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        # Per-model OpenRouter provider routing (model_id -> routing block). Pins
        # a model to one upstream for consistency and to avoid weak providers.
        self._provider_prefs = provider_prefs or {}
        # Retry transient failures with exponential backoff. This is what makes
        # running many games concurrently safe: parallel calls hit rate limits
        # (429) far more often, and an upstream may briefly 5xx or return a
        # 200-with-error body instead of choices. Persistent failures still
        # raise, and the runner turns that into a clean abstention.
        self._max_retries = max_retries
        self._backoff_base = backoff_base

    def _routing_for(self, model_id, provider_override) -> Optional[dict]:
        """The provider routing block to apply to this call.

        A per-call provider_override wins over the model-keyed provider_prefs.
        This is the Season 2 seam: several seats share ONE api model_id but need
        DIFFERENT routes (provider+quant), so the route cannot be looked up by
        model_id alone — the seat supplies it directly. When no override is
        given (Season 1), the model-keyed pin applies exactly as before.
        """
        if provider_override is not None:
            return provider_override
        return self._provider_prefs.get(model_id)

    def _build_payload(self, model_id, messages, temperature, max_tokens,
                       provider_override=None) -> dict:
        payload = {
            "model": model_id,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            # Ask OpenRouter to include cost in the usage block.
            "usage": {"include": True},
        }
        # Pin the upstream provider+quant for this call, if configured.
        routing = self._routing_for(model_id, provider_override)
        if routing:
            payload["provider"] = routing
        return payload

    def complete(
        self,
        model_id: str,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
        provider_override: Optional[dict] = None,
    ) -> CallResult:
        # Imported lazily so offline tests (fake transport) never need requests.
        import requests

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            # OpenRouter ranking headers; harmless and conventional.
            "HTTP-Referer": "https://howlarena.com",
            "X-Title": "Howl Arena",
        }
        payload = self._build_payload(model_id, messages, temperature, max_tokens,
                                      provider_override)
        url = f"{self._base_url}/chat/completions"

        last_error = "no attempt made"
        for attempt in range(self._max_retries + 1):
            if attempt:
                # Exponential backoff before a retry; safe to sleep in a worker
                # thread (it just yields while waiting on the rate limit).
                time.sleep(self._backoff_base * (2 ** (attempt - 1)))
            try:
                started = time.perf_counter()
                response = requests.post(url, headers=headers, json=payload, timeout=self._timeout)
                latency_s = time.perf_counter() - started
            except requests.exceptions.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                continue

            # Rate limit or server error: transient, worth retrying.
            if response.status_code == 429 or response.status_code >= 500:
                last_error = f"HTTP {response.status_code}"
                continue

            # Other 4xx (auth 401, payment 402, key-limit 403, bad request 400)
            # are TERMINAL — retrying wastes time and never helps. Fail fast.
            # (Retrying a mid-run 403 key-limit is what caused a 56-minute hang.)
            if response.status_code >= 400:
                raise RuntimeError(
                    f"OpenRouter HTTP {response.status_code}: {response.text[:160]}"
                )

            try:
                data = response.json()
            except ValueError as exc:
                last_error = f"bad JSON: {exc}"
                continue

            # OpenRouter sometimes returns HTTP 200 with an error body and no
            # choices (e.g. upstream hiccup). Treat as transient and retry.
            choices = data.get("choices")
            if not choices:
                last_error = f"no choices ({str(data.get('error') or '')[:80]})"
                continue

            usage = data.get("usage") or {}
            cost = usage.get("cost")
            routing = self._routing_for(model_id, provider_override) or {}
            enforced_quant = (routing.get("quantizations") or [None])[0]
            return CallResult(
                text=choices[0]["message"]["content"] or "",
                input_tokens=int(usage.get("prompt_tokens", 0) or 0),
                output_tokens=int(usage.get("completion_tokens", 0) or 0),
                cost=float(cost) if cost is not None else None,
                latency_s=latency_s,
                provider=data.get("provider"),
                served_model=data.get("model"),
                quant=enforced_quant,
            )

        raise RuntimeError(
            f"OpenRouter call for {model_id} failed after "
            f"{self._max_retries + 1} attempt(s): {last_error}"
        )
