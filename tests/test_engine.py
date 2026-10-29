"""End to end: text -> interpreter -> validation -> link -> real FL script (simulated FL API) -> result."""

import json


def test_acceptance_set_tempo_142(engine, sim):
    for text in ("SET_TEMPO 142", "set BPM to 142"):
        sim.fake.tempo_milli = 120000
        r = engine.handle_text(text, source="test")
        assert r["success"] and r["status"] == "OK", r
        assert r["command"] == "set_tempo" and r["parameters"] == {"bpm": 142}
        assert sim.fake.tempo_milli == 142000
        assert r["state"]["tempo"] == 142.0


def test_fractional_tempo(engine, sim):
    assert engine.handle_text("SET_TEMPO 87.5")["success"]
    assert sim.fake.tempo_milli == 87500


def test_play_stop_record_go_to_start(engine, sim):
    fake = sim.fake
    r = engine.handle_text("PLAY"); assert r["success"] and fake.playing
    r = engine.handle_text("PLAY"); assert r["success"] and fake.playing  # idempotent despite FL's toggle
    r = engine.handle_text("STOP"); assert r["success"] and not fake.playing
    r = engine.handle_text("RECORD"); assert r["success"] and fake.recording and fake.playing
    r = engine.handle_text("RECORD"); assert r["success"] and fake.recording  # does not toggle off
    fake.song_pos = 0.5
    r = engine.handle_text("GO_TO_START"); assert r["success"] and fake.song_pos == 0.0
    r = engine.handle_text("STOP"); assert r["success"] and not fake.playing


def test_status(engine):
    r = engine.handle_text("status")
    assert r["success"] and r["state"]["tempo"] == 140.0


def test_structured_json_command(engine, sim):
    r = engine.handle_command({"command": "set_tempo", "parameters": {"bpm": 150}, "request_id": "phone-1"})
    assert r["success"] and r["request_id"] == "phone-1" and sim.fake.tempo_milli == 150000


def test_invalid_input_never_reaches_fl(engine, sim):
    for text in ("SET_TEMPO 999", "SET_TEMPO 5", "format c:", '{"command": "shell"}'):
        r = engine.handle_text(text)
        assert not r["success"] and r["status"] in ("OUT_OF_RANGE", "PARSE_FAILED", "UNKNOWN_COMMAND")
    assert sim.fake.calls == []


def test_fl_not_responding(engine, sim):
    sim.fake.responsive = False
    r = engine.handle_text("PLAY")
    assert not r["success"] and r["status"] == "FL_TIMEOUT"
    sim.fake.responsive = True  # FL comes back: next command just works
    assert engine.handle_text("PLAY")["success"]


def test_ack_without_effect_is_not_success(engine, sim):
    sim.fake.ignore_tempo = True
    r = engine.handle_text("SET_TEMPO 142")
    assert not r["success"] and r["status"] == "NOT_CONFIRMED" and r["state"]["tempo"] == 140.0


def test_fl_side_rejects_bad_args_directly(sim):
    """Second line of defence: the FL script range-checks even if the bridge is bypassed."""
    from piratebox.fl import wire
    replies = []
    sim.open(replies.append)
    sim.script.OnSysEx(type("E", (), {"sysex": bytes([0xF0, *wire.encode(1, 1, wire.OP_SET_TEMPO, wire.u21(999000)), 0xF7]), "handled": False})())
    sim.script.OnSysEx(type("E", (), {"sysex": bytes([0xF0, *wire.encode(1, 2, 0x55), 0xF7]), "handled": False})())
    statuses = [wire.decode(r).payload[0] for r in replies if wire.decode(r).msg_type == wire.MT_RESPONSE]
    assert statuses == [wire.ST_BAD_ARGS, wire.ST_UNKNOWN_OPCODE]
    assert sim.fake.tempo_milli == 140000


def test_journal_records_every_stage(engine, tmp_path):
    rid = engine.handle_text("set bpm to 142", source="test")["request_id"]
    lines = [json.loads(l) for f in tmp_path.glob("*.jsonl") for l in f.read_text().splitlines()]
    stages = [l["stage"] for l in lines if l["request_id"] == rid]
    assert stages == ["input", "interpreted", "validated", "sent", "acknowledged", "result"]


# ---- Phase 6 ---------------------------------------------------------------

def test_undo_redo(engine, sim):
    fake = sim.fake
    r = engine.handle_text("REDO")
    assert not r["success"] and r["status"] == "NO_EFFECT" and r["message"] == "Nothing to redo"
    assert engine.handle_text("undo that")["success"] and fake.undo_last == 1
    assert engine.handle_text("UNDO")["success"] and fake.undo_last == 2
    r = engine.handle_text("UNDO")  # history exhausted
    assert r["status"] == "NO_EFFECT" and r["message"] == "Nothing to undo"
    assert engine.handle_text("REDO")["success"] and fake.undo_last == 1


def test_save(engine, sim):
    assert engine.handle_text("status")["state"]["unsaved_changes"] is True
    r = engine.handle_text("save the project")
    assert r["success"] and r["state"]["unsaved_changes"] is False


def test_save_untitled_project_is_not_reported_as_saved(engine, sim):
    sim.fake.untitled = True
    r = engine.handle_text("SAVE")
    assert not r["success"] and r["status"] == "NOT_CONFIRMED" and "Save As" in r["message"]


def test_metronome(engine, sim):
    fake = sim.fake
    r = engine.handle_text("TOGGLE_METRONOME")
    assert r["success"] and fake.metronome and r["message"] == "Metronome on"
    r = engine.handle_text("toggle metronome")
    assert r["success"] and not fake.metronome and r["message"] == "Metronome off"
    r = engine.handle_text("turn the metronome on")
    assert r["success"] and fake.metronome
    r = engine.handle_text("metronome on")  # already on: idempotent, FL's toggle not pressed
    assert r["success"] and fake.metronome
    assert engine.handle_command({"command": "set_metronome", "parameters": {"enabled": False}})["success"]
    assert not fake.metronome


def test_open_mixer(engine, sim):
    r = engine.handle_text("open the mixer")
    assert r["success"] and 0 in sim.fake.windows_visible and sim.fake.focused == 0


def test_fl_side_rejects_unknown_window(sim):
    from piratebox.fl import wire
    replies = []
    sim.open(replies.append)
    sim.script.OnSysEx(type("E", (), {"sysex": bytes([0xF0, *wire.encode(1, 3, wire.OP_SHOW_WINDOW, [99]), 0xF7]), "handled": False})())
    assert wire.decode(replies[-1]).payload[0] == wire.ST_BAD_ARGS
