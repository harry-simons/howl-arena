"""Offline tests for the model adapter — no network, no API spend.

A fake transport stands in for OpenRouter so prompt construction, strict
parsing, the reprompt-then-abstain policy, cost accumulation, and the
information boundary are all exercised deterministically.

Run: python -m tests.adapter_test  (from the werewolf_pkg directory)
"""

from __future__ import annotations

import re

from adapter import context, prompt
from adapter.agent import OpenRouterAgent
from adapter.config import AdapterConfig
from adapter.openrouter import CallResult, CostAccumulator
from adapter.parsing import parse_reply
from engine.game import GameConfig, GameState
from engine.runner import MatchRunner
from engine.types import Action, ActionOutcome, ActionType, Phase, Role, Stance


# ----- fake transports -----------------------------------------------------

class ScriptedTransport:
    """Returns canned reply texts in order. For testing reprompt/parse paths."""

    def __init__(self, replies: list[str]):
        self._replies = list(replies)
        self.calls = 0

    def complete(self, model_id, messages, temperature, max_tokens) -> CallResult:
        text = self._replies[self.calls] if self.calls < len(self._replies) else "{}"
        self.calls += 1
        return CallResult(text=text, input_tokens=100, output_tokens=20, cost=0.0001)


class PlayingTransport:
    """Phase-aware fake that returns a legal, well-formed action every turn, so a
    whole game can run through the real OpenRouterAgent without any network."""

    def complete(self, model_id, messages, temperature, max_tokens) -> CallResult:
        content = messages[-1]["content"]
        if "It is NIGHT" in content:
            target = self._first_target(content)
            if "to investigate" in content:
                text = f'{{"action": "investigate", "target": {target}, "private_reasoning": "peek"}}'
            elif "to protect" in content:
                text = f'{{"action": "protect", "target": {target}, "private_reasoning": "shield"}}'
            else:
                text = f'{{"action": "kill", "target": {target}, "private_reasoning": "thin the village"}}'
        elif "It is the VOTE" in content:
            target = self._first_target(content)
            text = f'{{"action": "vote", "target": {target}, "private_reasoning": "best read"}}'
        else:  # discussion
            text = ('{"action": "speak", "message": "Player 0 is too quiet.", '
                    '"stance": "attack", "lean": {"target": 0, "confidence": 0.6}, '
                    '"private_reasoning": "buying time"}')
        return CallResult(text=text, input_tokens=200, output_tokens=30, cost=0.0002)

    @staticmethod
    def _first_target(content: str) -> int:
        line = re.search(r"Legal targets: (.+)", content)
        nums = re.findall(r"\d+", line.group(1)) if line else []
        return int(nums[0]) if nums else 0


def _config() -> AdapterConfig:
    return AdapterConfig(model_id="fake/model", api_key="test")


def _seat_view(role: Role = Role.VILLAGER, phase: Phase = Phase.DAY_VOTE):
    """A minimal real PlayerView from a fresh game state."""
    state = GameState("t", seed=5, config=GameConfig(7, 2))
    state.assign_roles([f"m{i}" for i in range(7)])
    state.phase = phase
    # Find a seat with the requested role so role-specific prompts are valid.
    seat = next(s for s, p in state.players.items() if p.role is role)
    return state, state.build_player_view(seat)


# ----- parsing -------------------------------------------------------------

def test_parse_well_formed():
    _, view = _seat_view(Role.VILLAGER, Phase.DAY_VOTE)
    target = view.valid_target_seat_ids[0]
    result = parse_reply(
        f'{{"action": "vote", "target": {target}, "private_reasoning": "x"}}', view
    )
    assert result.action is not None and result.error is None
    assert result.action.action_type is ActionType.VOTE
    assert result.action.target_seat_id == target
    assert result.action.outcome is ActionOutcome.ACCEPTED
    print("[1] well-formed vote parses to an ACCEPTED action")


def test_parse_speak_captures_extras():
    _, view = _seat_view(Role.VILLAGER, Phase.DAY_DISCUSSION)
    reply = ('text before {"action": "speak", "message": "I distrust Player 2.", '
             '"stance": "attack", "lean": {"target": 2, "confidence": 1.5}, '
             '"private_reasoning": "plan"} trailing text')
    result = parse_reply(reply, view)
    a = result.action
    assert a is not None and a.action_type is ActionType.SPEAK
    assert a.stance is Stance.ATTACK
    assert a.lean_target_seat_id == 2
    assert a.lean_confidence == 1.0  # clamped into [0, 1]
    assert a.private_reasoning == "plan"
    print("[2] speak captures stance, lean (clamped), and private reasoning amid prose")


def test_parse_sanctioned_refusal():
    _, view = _seat_view(Role.WEREWOLF, Phase.NIGHT)
    result = parse_reply('{"action": "refuse", "private_reasoning": "no"}', view)
    assert result.action is not None
    assert result.action.outcome is ActionOutcome.REFUSED
    print("[3] sanctioned {action: refuse} maps to REFUSED")


def test_parse_prose_refusal():
    _, view = _seat_view(Role.WEREWOLF, Phase.NIGHT)
    result = parse_reply("As an AI, I cannot assist with eliminating a player.", view)
    assert result.action is not None
    assert result.action.outcome is ActionOutcome.REFUSED
    print("[4] prose refusal (no JSON) is classified REFUSED, not MALFORMED")


def test_parse_malformed():
    _, view = _seat_view(Role.VILLAGER, Phase.DAY_VOTE)
    result = parse_reply("I'll vote for the quiet one, probably.", view)
    assert result.action is None and result.error is not None
    print(f"[5] unparseable non-refusal yields an error to reprompt on: {result.error!r}")


def test_parse_phase_mismatch_is_error():
    _, view = _seat_view(Role.VILLAGER, Phase.DAY_VOTE)
    result = parse_reply('{"action": "speak", "message": "hi"}', view)
    assert result.action is None and result.error is not None
    print("[6] action that does not fit the phase is an error, not silently accepted")


# ----- agent reprompt policy ----------------------------------------------

def test_reprompt_then_succeeds():
    _, view = _seat_view(Role.VILLAGER, Phase.DAY_VOTE)
    target = view.valid_target_seat_ids[0]
    transport = ScriptedTransport([
        "no json here",  # malformed -> triggers one reprompt
        f'{{"action": "vote", "target": {target}, "private_reasoning": "ok"}}',
    ])
    cost = CostAccumulator()
    agent = OpenRouterAgent("fake/model", transport, _config(), cost)
    action = agent.get_action(view)
    assert action.action_type is ActionType.VOTE
    assert transport.calls == 2
    assert cost.calls == 2  # both calls are billed
    print("[7] one malformed reply triggers exactly one reprompt, then succeeds")


def test_reprompt_then_abstains_malformed():
    _, view = _seat_view(Role.VILLAGER, Phase.DAY_VOTE)
    transport = ScriptedTransport(["garbage", "still garbage"])
    cost = CostAccumulator()
    agent = OpenRouterAgent("fake/model", transport, _config(), cost)
    action = agent.get_action(view)
    assert action.action_type is ActionType.ABSTAIN
    assert action.outcome is ActionOutcome.MALFORMED
    assert transport.calls == 2  # no second reprompt
    print("[8] two malformed replies abstain as MALFORMED after a single reprompt")


def test_agent_calls_with_its_own_model_id():
    """Each seat must call the transport with ITS model, not the config default,
    or a mixed-model match would silently call one model for every seat."""
    class RecordingTransport:
        def __init__(self):
            self.seen = []

        def complete(self, model_id, messages, temperature, max_tokens):
            self.seen.append(model_id)
            return CallResult(text='{"action": "refuse", "private_reasoning": "x"}')

    _, view = _seat_view(Role.VILLAGER, Phase.DAY_VOTE)
    transport = RecordingTransport()
    # Config default is deliberately different from the seat's model.
    config = AdapterConfig(model_id="config/default", api_key="test")
    agent = OpenRouterAgent("seat/model-A", transport, config, CostAccumulator())
    agent.get_action(view)
    assert transport.seen == ["seat/model-A"], transport.seen
    print("[8b] agent calls the transport with its own model id, not the config default")


# ----- context and information boundary ------------------------------------

def test_facts_block_has_voting_history():
    config = GameConfig(7, 2)
    state = GameState("t", seed=5, config=config)
    state.assign_roles([f"m{i}" for i in range(7)])
    state.resolve_night()  # produces a night outcome event
    state.phase = Phase.DAY_VOTE
    voters = state.alive_seats()
    for v in voters:
        state.submit_action(Action(seat_id=v, action_type=ActionType.VOTE,
                                   target_seat_id=voters[0] if v != voters[0] else voters[1]))
    state.resolve_votes()
    view = state.build_player_view(state.alive_seats()[0])
    facts = context.render_facts(view)
    assert "Voting history:" in facts
    assert "Round 1:" in facts
    print("[9] facts block renders per-round voting history from engine events")


def test_private_reasoning_never_enters_prompt():
    config = GameConfig(7, 2)
    state = GameState("t", seed=5, config=config)
    state.assign_roles([f"m{i}" for i in range(7)])
    state.resolve_night()
    secret = "SECRET_PLAN_DO_NOT_LEAK"
    speaker = state.alive_seats()[0]
    state.submit_action(Action(
        seat_id=speaker,
        action_type=ActionType.SPEAK,
        message="Let us be calm.",
        private_reasoning=secret,
    ))
    # Build another seat's prompt; the secret must not appear anywhere in it.
    other = state.alive_seats()[1]
    messages = prompt.build_messages(state.build_player_view(other))
    blob = " ".join(m["content"] for m in messages)
    assert secret not in blob, "private reasoning leaked into another player's prompt"
    assert "Let us be calm." in blob  # the public message does carry through
    print("[10] private_reasoning never enters another player's prompt; public message does")


def test_prompt_does_not_leak_model_identity():
    _, view = _seat_view(Role.VILLAGER, Phase.DAY_DISCUSSION)
    messages = prompt.build_messages(view)
    blob = " ".join(m["content"] for m in messages)
    assert "m0" not in blob and "m1" not in blob  # model ids never shown
    assert "Player" in blob
    print("[11] prompt anonymises players; no model id leaks into the text")


# ----- full game offline ---------------------------------------------------

def test_full_game_through_agent():
    transport = PlayingTransport()
    cost = CostAccumulator()
    agents = [OpenRouterAgent("fake/model", transport, _config(), cost) for _ in range(7)]
    runner = MatchRunner(GameConfig(7, 2))
    state = runner.run("offline", seed=3, agents=agents)
    assert state.phase is Phase.ENDED
    assert state.result is not None
    record = state.to_record()
    record.cost = cost.to_game_cost()
    assert record.cost.calls > 0
    assert record.cost.input_tokens > 0
    print(f"[12] full game ran through real agent path -> {state.result.winner.value}, "
          f"{record.cost.calls} calls, {record.cost.input_tokens} input tokens")


# ----- v2: special roles ---------------------------------------------------

def test_v2_full_game_with_roles():
    """A 9p/3w + seer + healer game with 3 discussion rounds runs end to end
    through the real agent path, exercising investigate/protect parsing."""
    transport = PlayingTransport()
    cost = CostAccumulator()
    agents = [OpenRouterAgent("fake/model", transport, _config(), cost) for _ in range(9)]
    runner = MatchRunner(
        GameConfig(9, 3, num_seers=1, num_healers=1), discussion_rounds=3
    )
    state = runner.run("offline-v2", seed=4, agents=agents)
    assert state.phase is Phase.ENDED and state.result is not None
    # The seer and healer must have acted at night via investigate/protect.
    kinds = {a.action_type for a in state.actions if a.outcome is ActionOutcome.ACCEPTED}
    assert ActionType.INVESTIGATE in kinds, "seer never investigated"
    assert ActionType.PROTECT in kinds, "healer never protected"
    print(f"[13] v2 game (9p/3w + seer + healer, 3 rounds) ran -> {state.result.winner.value}")


def test_per_model_cost_recorded():
    """Per-model cost is captured and recorded, and reconciles with the game
    total — so the leaderboard's cost-per-model stat survives persistence."""
    transport = PlayingTransport()
    cost = CostAccumulator()
    agents = [OpenRouterAgent(f"m{i}", transport, _config(), cost) for i in range(9)]
    runner = MatchRunner(GameConfig(9, 2, num_seers=1, num_healers=1), discussion_rounds=2)
    state = runner.run("offline-cost", seed=4, agents=agents)
    record = state.to_record()
    record.cost = cost.to_game_cost()
    record.model_costs = cost.model_game_costs()
    assert record.model_costs, "no per-model costs recorded"
    assert sum(c.calls for c in record.model_costs.values()) == record.cost.calls
    assert abs(sum(c.total_cost for c in record.model_costs.values())
               - record.cost.total_cost) < 1e-9
    print(f"[17] per-model cost recorded for {len(record.model_costs)} models, reconciles with total")


def test_seer_knowledge_is_private():
    """The seer's investigation results appear in the seer's own prompt but in
    no other seat's prompt — the marquee hidden-information boundary."""
    state = GameState("t", seed=4, config=GameConfig(9, 3, num_seers=1, num_healers=1))
    state.assign_roles([f"m{i}" for i in range(9)])
    seer = state.alive_seers()[0]
    target = next(s for s in state.alive_seats() if s != seer)
    # Seer investigates; resolve the night so knowledge is recorded.
    state.submit_action(Action(seat_id=seer, action_type=ActionType.INVESTIGATE,
                               target_seat_id=target))
    state.resolve_night()

    seer_view = state.build_player_view(seer)
    assert target in seer_view.known_alignments, "seer did not retain its result"

    seer_blob = " ".join(m["content"] for m in prompt.build_messages(seer_view))
    assert f"Player {target} is" in seer_blob  # the seer sees its own result

    # No other living seat may see the seer's knowledge.
    for other in state.alive_seats():
        if other == seer:
            continue
        view = state.build_player_view(other)
        assert view.known_alignments == {}, f"seat {other} leaked seer knowledge"
        blob = " ".join(m["content"] for m in prompt.build_messages(view))
        assert "investigations so far" not in blob.lower()
    print("[14] seer's investigation results are private to the seer")


def test_role_revealed_on_death():
    """Deaths reveal the dead player's role to everyone (v2 role reveal)."""
    state = GameState("t", seed=4, config=GameConfig(9, 3, num_seers=1, num_healers=1))
    state.assign_roles([f"m{i}" for i in range(9)])
    state.phase = Phase.DAY_VOTE
    voters = state.alive_seats()
    victim = voters[0]
    for v in voters:
        state.submit_action(Action(seat_id=v, action_type=ActionType.VOTE,
                                   target_seat_id=victim if v != victim else voters[1]))
    state.resolve_votes()
    elim = next(e for e in state.events if e.kind == "eliminated")
    assert elim.revealed_role is not None
    assert "They were" in elim.detail
    print(f"[15] elimination reveals role: {elim.detail!r}")


def test_dead_target_vote_is_tracked():
    """Voting for an already-dead player is recorded as a precise 'target_dead'
    signal in the audit trail, not silently discarded — it measures how well a
    model tracks game state, which we want to analyze over time."""
    state = GameState("t", seed=4, config=GameConfig(9, 2, num_seers=1, num_healers=1))
    state.assign_roles([f"m{i}" for i in range(9)])
    state.players[1].alive = False  # Player 1 is dead
    state.phase = Phase.DAY_VOTE
    action = state.submit_action(
        Action(seat_id=0, action_type=ActionType.VOTE, target_seat_id=1)
    )
    assert action.outcome is ActionOutcome.ILLEGAL
    assert action.note.startswith("target_dead"), action.note
    assert action in state.actions  # persisted in the record
    print("[20] dead-player vote recorded as a trackable 'target_dead' signal")


def test_persistence_roundtrip_and_transcript():
    """A finished game serializes losslessly (incl. private reasoning and
    per-model cost) and renders a readable transcript pairing public lines with
    hidden reasoning."""
    import tempfile

    import storage

    transport = PlayingTransport()
    cost = CostAccumulator()
    agents = [OpenRouterAgent(f"m{i}", transport, _config(), cost) for i in range(9)]
    runner = MatchRunner(GameConfig(9, 2, num_seers=1, num_healers=1), discussion_rounds=2)
    state = runner.run("persist-test", seed=4, agents=agents)
    record = state.to_record()
    record.cost = cost.to_game_cost()
    record.model_costs = cost.model_game_costs()
    record.prompt_version = "s2-v1"
    record.season_id = "test-season"

    with tempfile.TemporaryDirectory() as tmp:
        path = storage.save_game(record, base_dir=tmp)
        assert path.exists() and path.with_suffix(".txt").exists()
        loaded = storage.load_record(path)

    # Lossless on the things that matter for replay/scoring.
    assert loaded["seed"] == 4
    assert loaded["result"]["winner"] == record.result.winner.value
    assert len(loaded["actions"]) == len(record.actions)
    assert loaded["model_costs"], "per-model cost missing from stored record"
    assert any(a.get("private_reasoning") for a in loaded["actions"]), "private reasoning lost"
    assert all(a.get("phase") for a in loaded["actions"]), "action timeline (phase) lost"

    transcript_text = storage.render_transcript(record)
    assert "thought:" in transcript_text  # the said-vs-thought pairing
    assert "NIGHT" in transcript_text and "VOTE" in transcript_text
    print("[18] game persists losslessly (private reasoning + cost) and renders a transcript")


def test_provider_pin_injected_into_request():
    """A pinned model gets the provider routing block in its request; an unpinned
    one does not. (The pin keeps a model on one upstream/quant for consistency.)"""
    from adapter.openrouter import OpenRouterClient

    prefs = {"lab/pinned": {"order": ["Alibaba"], "allow_fallbacks": False}}
    client = OpenRouterClient("k", "https://x", 10, provider_prefs=prefs)
    pinned = client._build_payload("lab/pinned", [], 0.5, 100)
    assert pinned["provider"] == {"order": ["Alibaba"], "allow_fallbacks": False}
    unpinned = client._build_payload("lab/other", [], 0.5, 100)
    assert "provider" not in unpinned
    print("[19] provider pin injected into the request for configured models only")


def test_s2_seat_decouples_label_from_api_model_and_route():
    """Season 2 seam: a seat's DISPLAY label is its identity (cost/record key),
    while the API call uses a separate api_model_id and a per-seat routing block.
    Two seats sharing one api model but different routes must stay distinct."""
    class RecordingTransport:
        def __init__(self):
            self.seen = []  # (model_id, provider_override) per call

        def complete(self, model_id, messages, temperature, max_tokens,
                     provider_override=None):
            self.seen.append((model_id, provider_override))
            return CallResult(text='{"action": "refuse", "private_reasoning": "x"}',
                              input_tokens=10, output_tokens=2, cost=0.0001)

    _, view = _seat_view(Role.VILLAGER, Phase.DAY_VOTE)
    transport = RecordingTransport()
    cost = CostAccumulator()
    route_fp16 = {"order": ["Cerebras"], "quantizations": ["fp16"], "allow_fallbacks": False}
    route_fp4 = {"order": ["WandB"], "quantizations": ["fp4"], "allow_fallbacks": False}
    seat_a = OpenRouterAgent("gpt-oss-120b@Cerebras-fp16", transport, _config(), cost,
                             api_model_id="openai/gpt-oss-120b", routing=route_fp16)
    seat_b = OpenRouterAgent("gpt-oss-120b@WandB-fp4", transport, _config(), cost,
                             api_model_id="openai/gpt-oss-120b", routing=route_fp4)
    seat_a.get_action(view)
    seat_b.get_action(view)

    # Both seats call the SAME api model, with their OWN route.
    assert transport.seen == [
        ("openai/gpt-oss-120b", route_fp16),
        ("openai/gpt-oss-120b", route_fp4),
    ], transport.seen
    # Identity/cost is keyed by the DISPLAY label, not the shared api model.
    assert set(cost.per_model) == {"gpt-oss-120b@Cerebras-fp16", "gpt-oss-120b@WandB-fp4"}
    print("[22] S2 seat: label is identity, api model + per-seat route are decoupled")


def test_s2_provider_override_wins_over_prefs_and_sets_quant():
    """The transport applies a per-call provider_override over any model-keyed
    pin (so same-model S2 seats route differently), and the enforced quant comes
    from whichever route applied — recorded onto the CallResult."""
    from adapter.openrouter import OpenRouterClient

    # A model-keyed pin exists, but the per-call override must win.
    prefs = {"openai/gpt-oss-120b": {"order": ["Cerebras"], "quantizations": ["fp16"],
                                     "allow_fallbacks": False}}
    client = OpenRouterClient("k", "https://x", 10, provider_prefs=prefs)
    override = {"order": ["WandB"], "quantizations": ["fp4"], "allow_fallbacks": False}

    payload = client._build_payload("openai/gpt-oss-120b", [], 0.5, 100,
                                    provider_override=override)
    assert payload["provider"] == override, payload.get("provider")
    # Without an override, the model-keyed pin still applies (S1 path unchanged).
    s1_payload = client._build_payload("openai/gpt-oss-120b", [], 0.5, 100)
    assert s1_payload["provider"] == prefs["openai/gpt-oss-120b"]
    # The quant the transport will stamp onto CallResult follows the applied route.
    assert client._routing_for("openai/gpt-oss-120b", override)["quantizations"] == ["fp4"]
    assert client._routing_for("openai/gpt-oss-120b", None)["quantizations"] == ["fp16"]
    print("[23] per-call provider_override beats model pin; enforced quant tracks the route")


def test_void_detection():
    """A normal completed game is scored; a degenerate one (most turns failed) is
    flagged void so it is excluded from ratings."""
    import scoring

    transport = PlayingTransport()
    cost = CostAccumulator()
    agents = [OpenRouterAgent(f"m{i}", transport, _config(), cost) for i in range(9)]
    runner = MatchRunner(GameConfig(9, 2, num_seers=1, num_healers=1), discussion_rounds=2)
    rec = runner.run("void-test", seed=4, agents=agents).to_record()

    scoring.assess_void(rec)
    assert rec.void is False, f"normal game wrongly voided: {rec.void_reason}"
    for a in rec.actions:                      # now make every turn a failure
        a.outcome = ActionOutcome.TIMEOUT
    scoring.assess_void(rec)
    assert rec.void is True and rec.void_reason
    print(f"[21] void detection: normal game kept; all-failed game voided ({rec.void_reason})")


def test_concurrent_games_are_independent():
    """Several games run concurrently through one shared transport produce valid,
    independent results — the basis for parallel calibration runs. The shared
    transport is stateless and each game has its own accumulator, so no locks."""
    from concurrent.futures import ThreadPoolExecutor

    transport = PlayingTransport()  # stateless; safe to share across threads
    cfg = _config()
    runner = MatchRunner(GameConfig(9, 2, num_seers=1, num_healers=1), discussion_rounds=2)

    def play(seed):
        cost = CostAccumulator()
        agents = [OpenRouterAgent(f"m{i}", transport, cfg, cost) for i in range(9)]
        state = runner.run(f"c{seed}", seed, agents)
        return state.result, cost.calls

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(play, range(4)))
    assert all(result is not None and calls > 0 for result, calls in results)
    print(f"[16] {len(results)} games ran concurrently, all completed independently")


if __name__ == "__main__":
    test_parse_well_formed()
    test_parse_speak_captures_extras()
    test_parse_sanctioned_refusal()
    test_parse_prose_refusal()
    test_parse_malformed()
    test_parse_phase_mismatch_is_error()
    test_reprompt_then_succeeds()
    test_reprompt_then_abstains_malformed()
    test_agent_calls_with_its_own_model_id()
    test_facts_block_has_voting_history()
    test_private_reasoning_never_enters_prompt()
    test_prompt_does_not_leak_model_identity()
    test_full_game_through_agent()
    test_v2_full_game_with_roles()
    test_per_model_cost_recorded()
    test_seer_knowledge_is_private()
    test_role_revealed_on_death()
    test_dead_target_vote_is_tracked()
    test_persistence_roundtrip_and_transcript()
    test_provider_pin_injected_into_request()
    test_s2_seat_decouples_label_from_api_model_and_route()
    test_s2_provider_override_wins_over_prefs_and_sets_quant()
    test_void_detection()
    test_concurrent_games_are_independent()
    print("\nAll adapter checks passed.")
