"""v0.5 commands end to end: text -> rules -> validation -> link -> real FL script (simulated FL API)."""


def ok(engine, text):
    r = engine.handle_text(text, source="test")
    assert r["success"], r
    return r


def fail(engine, text, status):
    r = engine.handle_text(text, source="test")
    assert not r["success"] and r["status"] == status, r
    return r


def test_windows(engine, sim):
    ok(engine, "open the channel rack")
    assert 1 in sim.fake.windows_visible and sim.fake.focused == 1
    ok(engine, "piano roll")
    assert 3 in sim.fake.windows_visible and sim.fake.focused == 3


def test_patterns(engine, sim):
    fake = sim.fake
    fail(engine, "previous pattern", "NO_EFFECT")  # already on pattern 1
    ok(engine, "next pattern"); assert fake.pattern == 2
    ok(engine, "next pattern"); assert fake.pattern == 3
    r = fail(engine, "next pattern", "NO_EFFECT"); assert "last pattern" in r["message"]
    ok(engine, "previous pattern"); assert fake.pattern == 2
    ok(engine, "duplicate pattern"); assert fake.pattern_count == 4 and fake.pattern == 4
    fake.can_clone = False
    fail(engine, "clone pattern", "NO_EFFECT")


def test_mute_and_solo_are_idempotent(engine, sim):
    fake = sim.fake
    r = ok(engine, "mute track 3"); assert r["message"] == "Track 3 muted" and fake.track_muted == {3}
    ok(engine, "mute track 3"); assert fake.track_muted == {3}  # FL's muteTrack toggles; we don't
    ok(engine, "unmute track 3"); assert fake.track_muted == set()
    r = ok(engine, "solo master"); assert r["message"] == "Master soloed" and fake.track_solo == {0}
    ok(engine, "unsolo master"); assert fake.track_solo == set()


def test_select_track_and_range(engine, sim):
    ok(engine, "select track 12"); assert sim.fake.selected_track == 12
    fail(engine, "select track 126", "OUT_OF_RANGE")
    assert not any(c[0] == "setTrackNumber" and c[1] == 126 for c in sim.fake.calls)


def test_volume_nudge_and_limits(engine, sim):
    fake = sim.fake
    ok(engine, "turn up track 2"); assert abs(fake.track_volume[2] - 0.85) < 1e-9
    ok(engine, "track 2 down"); ok(engine, "track 2 down"); assert abs(fake.track_volume[2] - 0.75) < 1e-9
    fake.track_volume[5] = 1.0
    r = fail(engine, "volume up on track 5", "NO_EFFECT"); assert "full volume" in r["message"]
    fake.track_volume[5] = 0.02
    ok(engine, "track 5 down"); assert fake.track_volume[5] == 0.0


def test_move_bars(engine, sim):
    fake = sim.fake  # 16-bar song
    fake.song_pos = 0.0
    fail(engine, "go back 1 bar", "NO_EFFECT")
    r = ok(engine, "forward 4 bars"); assert r["message"] == "Forward 4 bars" and fake.song_pos == 0.25
    ok(engine, "back 1 bar"); assert fake.song_pos == 0.1875
    ok(engine, "skip forward 64 bars"); assert fake.song_pos == 1.0
    fail(engine, "forward 65 bars", "OUT_OF_RANGE")
    fake.song_bars = 0
    fail(engine, "back 2 bars", "NO_EFFECT")


def test_quantize_selected_channel(engine, sim):
    sim.fake.selected_channel = 2
    ok(engine, "quantise"); assert sim.fake.quantized == [2]
    sim.fake.selected_channel = -1
    r = fail(engine, "quantize", "NO_EFFECT"); assert "No channel" in r["message"]


def test_select_channel_is_one_based(engine, sim):
    ok(engine, "channel 1"); assert sim.fake.selected_channel == 0
    ok(engine, "select channel 4"); assert sim.fake.selected_channel == 3
    r = fail(engine, "channel 5", "FL_REJECTED"); assert "no channel 5" in r["message"]
    fail(engine, "channel 0", "OUT_OF_RANGE")


def test_structured_params_are_validated(engine, sim):
    for raw, status in [
        ({"command": "mute_track", "parameters": {"track": 3}}, "INVALID_PARAMETERS"),
        ({"command": "mute_track", "parameters": {"track": 3, "muted": "yes"}}, "INVALID_PARAMETERS"),
        ({"command": "volume_up", "parameters": {"track": 2.5}}, "INVALID_PARAMETERS"),
        ({"command": "go_back_bars", "parameters": {"bars": 0}}, "OUT_OF_RANGE"),
    ]:
        r = engine.handle_command(raw)
        assert not r["success"] and r["status"] == status, (raw, r)
    assert sim.fake.calls == []
