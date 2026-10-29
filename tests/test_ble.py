"""Bluetooth framing and dispatch. The radio itself needs real hardware; everything above it is tested here."""

import json

from piratebox.inputs.ble import FrameAssembler, encode_frames, handle_line
from piratebox.pairing import pairing_uri

TOKEN = "s3cret-token-0123456789"


def roundtrip(message, size):
    assembler = FrameAssembler()
    lines = []
    for chunk in encode_frames(message, size):
        assert len(chunk) <= size
        lines += assembler.feed(chunk)
    return lines


def test_frames_reassemble_at_any_chunk_size():
    msg = {"type": "text", "text": "mute track 3 — und dann 142 BPM ♪", "token": TOKEN}
    for size in (1, 2, 3, 7, 20, 180, 512):
        lines = roundtrip(msg, size)
        assert [json.loads(line) for line in lines] == [msg]  # multi-byte UTF-8 survives splitting


def test_several_messages_in_one_write_and_blank_lines():
    a = FrameAssembler()
    assert a.feed(b'{"a":1}\n\n{"b"') == ['{"a":1}']
    assert a.feed(b':2}\n') == ['{"b":2}']


def test_oversized_garbage_is_dropped():
    a = FrameAssembler(max_line=100)
    assert a.feed(b"x" * 150) == []
    assert a.feed(b'{"ok":true}\n') == ['{"ok":true}']  # recovers for the next message


def test_token_is_required(engine, sim):
    for line in ('{"type":"text","text":"play"}', '{"type":"text","text":"play","token":"nope"}',
                 '{"type":"text","text":"play","token":42}'):
        r = handle_line(engine, TOKEN, line)
        assert r["status"] == "UNAUTHORIZED" and not r["success"]
    assert not sim.fake.playing and sim.fake.calls == []


def test_authorised_messages_reach_the_engine(engine, sim):
    r = handle_line(engine, TOKEN, json.dumps({"token": TOKEN, "type": "voice_command", "text": "mute track 2"}))
    assert r["success"] and r["type"] == "command_result" and sim.fake.track_muted == {2}
    r = handle_line(engine, TOKEN, json.dumps({"token": TOKEN, "type": "command", "command": "play"}))
    assert r["success"] and sim.fake.playing  # token is stripped before strict validation
    r = handle_line(engine, TOKEN, json.dumps({"token": TOKEN, "type": "status"}))
    assert r["type"] == "status" and r["connected"]


def test_bad_json_is_reported_not_raised(engine):
    assert handle_line(engine, TOKEN, "{nope")["status"] == "INVALID_MESSAGE"
    assert handle_line(engine, TOKEN, "[1,2]")["status"] == "INVALID_MESSAGE"


def test_pairing_uri_carries_ble_name_only_when_enabled():
    assert "ble=" not in pairing_uri("10.0.0.2", 7142, TOKEN)
    assert pairing_uri("10.0.0.2", 7142, TOKEN, ble="PirateBox").endswith("&ble=PirateBox")
