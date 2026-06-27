"""The OpenRouter-backed player agent.

Implements the engine's PlayerAgent seam (model_id + get_action), so it drops
into the existing MatchRunner unchanged. It owns the one-reprompt-then-abstain
policy: a malformed reply gets a single corrective reprompt, and if that still
cannot be parsed the seat abstains as MALFORMED. Refusals are returned directly.

All cost is recorded into a shared CostAccumulator so the whole game's spend can
be attached to the GameRecord.
"""

from __future__ import annotations

from engine.types import Action, ActionOutcome, ActionType
from engine.views import PlayerView

from . import parsing, prompt
from .config import AdapterConfig
from .openrouter import CallResult, CostAccumulator, Transport


class OpenRouterAgent:
    """A single seat played by one model over an OpenRouter-compatible transport."""

    def __init__(
        self,
        model_id: str,
        transport: Transport,
        config: AdapterConfig,
        cost: CostAccumulator,
        api_model_id: str | None = None,
        routing: dict | None = None,
        prompt_variant: "prompt.PromptVariant | None" = None,
    ):
        # `model_id` is the seat's DISPLAY IDENTITY — what the runner reads into
        # seat_models and what the scorer/site key off. In Season 1 it equals the
        # API model. In Season 2 the 9 seats share one API model but each needs a
        # distinct label (e.g. "gpt-oss-120b@DeepInfra-fp8"), so identity and the
        # API call are decoupled here:
        #   - api_model_id: the model string actually sent to OpenRouter.
        #   - routing: this seat's provider+quant route (overrides the model-keyed
        #     PROVIDER_PREFS pin, which can't tell two same-model seats apart).
        self.model_id = model_id
        self._api_model_id = api_model_id or model_id
        self._routing = routing
        # Season 3 seam: a per-seat system-prompt variant. None => the frozen
        # module prompt (the Season 1 / Season 2 path, unchanged). When set, this
        # seat's standing system prompt is the only thing that differs from a
        # baseline seat — the user message (game state) and reprompt are shared.
        self._prompt = prompt_variant
        self._transport = transport
        self._config = config
        self._cost = cost

    def _call(self, messages: list[dict]) -> CallResult:
        kwargs = dict(
            # The seat's own API model, not the config default — this is what lets
            # a single match seat several different models (Step 3) and, in S2,
            # several routes of the SAME model.
            model_id=self._api_model_id,
            messages=messages,
            temperature=self._config.temperature,
            max_tokens=self._config.max_tokens,
        )
        # Only pass the per-seat route when one is set, so the Season 1 path (and
        # the offline fake transports) keep their original call signature.
        if self._routing is not None:
            kwargs["provider_override"] = self._routing
        result = self._transport.complete(**kwargs)
        # Cost/identity are keyed by the DISPLAY label, never the API model — so
        # the per-seat (provider·quant) breakdown survives into the record.
        self._cost.add(result, model_id=self.model_id)
        return result

    def get_action(self, view: PlayerView) -> Action:
        # Use this seat's prompt variant if it has one (Season 3), else the
        # frozen module prompt (Season 1 / Season 2). Both expose the same
        # build_messages / build_reprompt_correction surface.
        pv = self._prompt or prompt
        messages = pv.build_messages(view)
        result = self._call(messages)
        parsed = parsing.parse_reply(result.text, view)
        if parsed.action is not None:
            parsed.action.provider = result.provider
            parsed.action.quant = result.quant
            return parsed.action

        # One reprompt, carrying the parse error, before abstaining.
        messages = messages + [
            {"role": "assistant", "content": result.text},
            pv.build_reprompt_correction(parsed.error or "unparseable reply"),
        ]
        result = self._call(messages)
        reparsed = parsing.parse_reply(result.text, view)
        if reparsed.action is not None:
            reparsed.action.provider = result.provider
            reparsed.action.quant = result.quant
            return reparsed.action

        return Action(
            seat_id=view.your_seat_id,
            action_type=ActionType.ABSTAIN,
            outcome=ActionOutcome.MALFORMED,
            note=f"unparseable after reprompt: {reparsed.error}",
            provider=result.provider,
            quant=result.quant,
        )
