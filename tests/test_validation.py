import pytest

from piratebox.engine import ACTIONS
from piratebox.errors import BridgeError, Status


def reject(registry, raw):
    with pytest.raises(BridgeError) as e:
        registry.validate(raw)
    return e.value.status


def test_whitelist_matches_implementation(registry):
    assert set(registry.commands) == set(ACTIONS)


def test_status_codes_match_schema(registry):
    assert set(registry.status_codes) == {s.value for s in Status}


def test_valid_tempo(registry):
    cmd = registry.validate({"command": "set_tempo", "parameters": {"bpm": 142}})
    assert cmd.name == "set_tempo" and cmd.parameters == {"bpm": 142}


def test_tempo_decimals_rounded(registry):
    assert registry.validate({"command": "set_tempo", "parameters": {"bpm": 140.12345}}).parameters["bpm"] == 140.123


@pytest.mark.parametrize("bpm", [19.9, 300.1, 0, -5, 1e9])
def test_tempo_out_of_range(registry, bpm):
    assert reject(registry, {"command": "set_tempo", "parameters": {"bpm": bpm}}) == Status.OUT_OF_RANGE


@pytest.mark.parametrize("bpm", ["142", True, None, float("nan"), float("inf"), [142]])
def test_tempo_wrong_type(registry, bpm):
    assert reject(registry, {"command": "set_tempo", "parameters": {"bpm": bpm}}) == Status.INVALID_PARAMETERS


@pytest.mark.parametrize("raw,status", [
    ({"command": "shell", "parameters": {"cmd": "rm -rf /"}}, Status.UNKNOWN_COMMAND),
    ({"command": "add_plugin"}, Status.UNKNOWN_COMMAND),  # planned, not enabled
    ({"command": "set_metronome", "parameters": {"enabled": 1}}, Status.INVALID_PARAMETERS),
    ({"command": "set_metronome"}, Status.INVALID_PARAMETERS),
    ({"command": "set_tempo"}, Status.INVALID_PARAMETERS),
    ({"command": "set_tempo", "parameters": {"bpm": 140, "x": 1}}, Status.INVALID_PARAMETERS),
    ({"command": "play", "parameters": {"loud": True}}, Status.INVALID_PARAMETERS),
    ({"command": "play", "exec": "x"}, Status.INVALID_MESSAGE),
    ({"command": 5}, Status.INVALID_MESSAGE),
    ({"command": "play", "confirmed": "yes"}, Status.INVALID_MESSAGE),
    (["play"], Status.INVALID_MESSAGE),
])
def test_rejections(registry, raw, status):
    assert reject(registry, raw) == status
