import pytest

from piratebox.interpreters import RuleInterpreter

TEMPO = {"command": "set_tempo", "parameters": {"bpm": 142.0}}


@pytest.mark.parametrize("text", [
    "SET_TEMPO 142", "set_tempo 142", "set tempo 142", "set BPM to 142", "set the tempo to 142",
    "tempo 142", "bpm 142", "142 bpm", "142bpm", "  Set   BPM   to 142. ", "change the tempo to 142 bpm",
])
def test_tempo_phrases(text):
    assert RuleInterpreter()._match(text) == TEMPO


@pytest.mark.parametrize("text,command", [
    ("PLAY", "play"), ("play", "play"), ("Stop", "stop"), ("RECORD", "record"),
    ("start recording", "record"), ("GO_TO_START", "go_to_start"), ("go to start", "go_to_start"),
    ("status", "get_status"), ("UNDO", "undo"), ("undo that", "undo"), ("redo", "redo"),
    ("SAVE", "save"), ("save the project", "save"), ("TOGGLE_METRONOME", "toggle_metronome"),
    ("OPEN_MIXER", "open_mixer"), ("open the mixer", "open_mixer"),
])
def test_simple_commands(text, command):
    assert RuleInterpreter()._match(text) == {"command": command}


@pytest.mark.parametrize("text,enabled", [
    ("metronome on", True), ("turn the metronome on", True), ("SET_METRONOME off", False),
    ("turn off the metronome", False), ("click on", True), ("switch the metronome off", False),
])
def test_metronome_on_off(text, enabled):
    assert RuleInterpreter()._match(text) == {"command": "set_metronome", "parameters": {"enabled": enabled}}


@pytest.mark.parametrize("text", ["", "delete everything", "set tempo to fast", "play the drums louder", "{bad json"])
def test_not_understood(text):
    assert RuleInterpreter()._match(text) is None


def test_json_passthrough():
    assert RuleInterpreter()._match('{"command": "play"}') == {"command": "play"}
