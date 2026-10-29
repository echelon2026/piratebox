"""AI interpreter with a scripted provider (no network). Live checks: python -m piratebox.ai.check"""

import pytest

from piratebox.ai.interpreter import AIInterpreter, build_schema, build_system_prompt
from piratebox.ai.provider import ProviderResult
from piratebox.commands import CommandRegistry
from piratebox.engine import CommandEngine
from piratebox.errors import BridgeError, Status
from piratebox.fl.controller import FLController
from piratebox.fl.link import FLLink
from piratebox.interpreters import ChainInterpreter, RuleInterpreter
from piratebox.journal import Journal


class ScriptedProvider:
    """Returns canned model outputs, records what it was asked."""
    name = "scripted"

    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = []

    def complete_json(self, system, user, schema):
        self.calls.append({"system": system, "user": user, "schema": schema})
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return ProviderResult(data=out, details={"provider": "scripted", "input_tokens": 900, "output_tokens": 20})


def cmd(name, **params):
    return {"result": {"command": name, "parameters": params}}


def unknown(msg):
    return {"result": {"command": "unknown", "clarification": msg}}


@pytest.fixture
def make_engine(sim, registry, tmp_path):
    links = []

    def make(provider):
        link = FLLink(sim, timeout=0.5)
        links.append(link)
        interpreter = ChainInterpreter(RuleInterpreter(), AIInterpreter(provider, registry))
        return CommandEngine(FLController(link), registry, interpreter, Journal(tmp_path))
    yield make
    for link in links:
        link.close()


def test_schema_only_allows_whitelisted_commands(registry):
    schema = build_schema(registry)
    branches = schema["properties"]["result"]["anyOf"]
    names = [b["properties"]["command"]["const"] for b in branches]
    assert sorted(names) == sorted([*registry.commands, "unknown"])
    for b in branches:
        assert b["additionalProperties"] is False
        if "parameters" in b["properties"]:
            assert b["properties"]["parameters"]["additionalProperties"] is False
    tempo = next(b for b in branches if b["properties"]["command"]["const"] == "set_tempo")
    assert tempo["properties"]["parameters"]["properties"] == {"bpm": {"type": "number"}}


def test_prompt_lists_every_command_with_ranges(registry):
    prompt = build_system_prompt(registry)
    for name in registry.commands:
        assert f"- {name}(" in prompt
    assert "bpm: number 20-300" in prompt


def test_conversational_tempo(make_engine, sim):
    provider = ScriptedProvider(cmd("set_tempo", bpm=140))
    r = make_engine(provider).handle_text("Let's make this one 140.")
    assert r["success"] and r["interpreter"] == "ai" and sim.fake.tempo_milli == 140000
    assert "<spoken_text>Let's make this one 140.</spoken_text>" in provider.calls[0]["user"]


def test_exact_commands_never_call_the_ai(make_engine, sim):
    provider = ScriptedProvider()
    engine = make_engine(provider)
    for text in ("SET_TEMPO 142", "PLAY", "stop", "undo"):
        r = engine.handle_text(text)
        assert r["interpreter"] == "rules"
    assert provider.calls == []


def test_ai_output_is_still_validated(make_engine, sim):
    provider = ScriptedProvider(cmd("set_tempo", bpm=900), {"result": {"command": "shell", "parameters": {"cmd": "rm"}}})
    engine = make_engine(provider)
    r = engine.handle_text("crank it to nine hundred")
    assert not r["success"] and r["status"] == "OUT_OF_RANGE"
    r = engine.handle_text("do something sneaky")
    assert not r["success"] and r["status"] == "UNKNOWN_COMMAND"
    assert sim.fake.calls == []


def test_clarification(make_engine, sim):
    provider = ScriptedProvider(unknown("I can't edit hi-hat patterns yet. I can change tempo, transport and the metronome."))
    r = make_engine(provider).handle_text("Make the hi-hats twice as fast.")
    assert not r["success"] and r["status"] == "NEEDS_CLARIFICATION" and r["requires_clarification"] is True
    assert "hi-hat" in r["message"] and sim.fake.calls == []


def test_empty_clarification_gets_default(make_engine):
    r = make_engine(ScriptedProvider(unknown("  "))).handle_text("mmm")
    assert r["status"] == "NEEDS_CLARIFICATION" and r["message"]


def test_ai_gets_current_state_for_relative_changes(make_engine, sim):
    provider = ScriptedProvider(cmd("set_tempo", bpm=150))
    engine = make_engine(provider)
    engine.handle_text("SET_TEMPO 140")  # rules path; engine now knows the tempo
    r = engine.handle_text("bump it up ten")
    assert r["success"] and sim.fake.tempo_milli == 150000
    assert "tempo 140 BPM" in provider.calls[0]["user"]


def test_ai_failure_is_reported_not_silent(make_engine, sim):
    provider = ScriptedProvider(BridgeError(Status.AI_UNAVAILABLE, "AI interpreter: cannot reach the AI service"))
    r = make_engine(provider).handle_text("let's hear it")
    assert not r["success"] and r["status"] == "AI_UNAVAILABLE" and "cannot reach" in r["message"]


def test_malformed_provider_output(make_engine):
    r = make_engine(ScriptedProvider({"nope": 1})).handle_text("play it")
    assert r["status"] == "AI_UNAVAILABLE"


def test_ai_details_journaled(make_engine, tmp_path):
    import json
    make_engine(ScriptedProvider(cmd("play"))).handle_text("let's hear it")
    lines = [json.loads(l) for f in tmp_path.glob("*.jsonl") for l in f.read_text().splitlines()]
    interpreted = next(l for l in lines if l["stage"] == "interpreted")
    assert interpreted["interpreter"] == "ai" and interpreted["input_tokens"] == 900 and "latency_ms" in interpreted


def test_claude_provider_maps_sdk_errors():
    """ClaudeProvider turns SDK failures into AI_UNAVAILABLE (uses a stub client, no network)."""
    anthropic = pytest.importorskip("anthropic")
    from piratebox.ai.provider import ClaudeProvider

    provider = ClaudeProvider()

    class Boom:
        def __init__(self, exc):
            self.exc = exc

        def create(self, **kwargs):
            raise self.exc

    for exc in (TypeError("Could not resolve authentication method"),):
        provider.client = type("C", (), {"messages": Boom(exc)})()
        with pytest.raises(BridgeError) as e:
            provider.complete_json("s", "u", {})
        assert e.value.status == Status.AI_UNAVAILABLE and "ANTHROPIC_API_KEY" in e.value.message


def test_claude_provider_request_shape():
    """The request uses structured outputs with our schema and the Haiku model."""
    pytest.importorskip("anthropic")
    from types import SimpleNamespace

    from piratebox.ai.provider import ClaudeProvider

    seen = {}

    def create(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(
            model="claude-haiku-4-5", stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=1000, output_tokens=15),
            content=[SimpleNamespace(type="text", text='{"result": {"command": "play", "parameters": {}}}')],
        )

    provider = ClaudeProvider()
    provider.client = SimpleNamespace(messages=SimpleNamespace(create=create))
    out = provider.complete_json("sys", "user", {"type": "object"})
    assert out.data == {"result": {"command": "play", "parameters": {}}}
    assert seen["model"] == "claude-haiku-4-5"
    assert seen["output_config"] == {"format": {"type": "json_schema", "schema": {"type": "object"}}}
    assert seen["system"] == "sys" and seen["messages"] == [{"role": "user", "content": "user"}]
