"""HTTP input source (Phase 8 test client / v0.2 local-network path) against the simulated FL."""

import json
import urllib.error
import urllib.request

import pytest

from piratebox.inputs.http import HttpInput


@pytest.fixture
def server(engine):
    http = HttpInput(engine, host="127.0.0.1", port=0, token="secret-token")
    http.start()
    yield http
    http.stop()


def post(server, body, token="secret-token", raw=None):
    port = server._server.server_port
    data = raw if raw is not None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/message", data=data, method="POST",
                                 headers={"Content-Type": "application/json", "X-PirateBox-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_button_command(server, sim):
    code, r = post(server, {"type": "command", "command": "set_tempo", "parameters": {"bpm": 142}})
    assert code == 200 and r["type"] == "command_result" and r["success"] and sim.fake.tempo_milli == 142000


def test_text_and_voice_command(server, sim):
    assert post(server, {"type": "text", "text": "PLAY"})[1]["success"] and sim.fake.playing
    assert post(server, {"type": "voice_command", "text": "stop"})[1]["success"] and not sim.fake.playing


def test_status(server):
    code, r = post(server, {"type": "status"})
    assert code == 200 and r["type"] == "status" and r["connected"] and r["state"]["tempo"] == 140.0


def test_status_when_fl_down(server, sim):
    sim.fake.responsive = False
    r = post(server, {"type": "status"})[1]
    assert r["connected"] is False and r["status"] == "FL_TIMEOUT"


def test_bad_token_rejected(server, sim):
    code, r = post(server, {"type": "command", "command": "play"}, token="wrong")
    assert code == 401 and r["status"] == "UNAUTHORIZED" and not sim.fake.playing


@pytest.mark.parametrize("body,raw", [
    (None, b"not json"), (None, b"x" * 5000), (["play"], None), ({"type": "shell", "cmd": "dir"}, None),
    ({"type": "text", "text": ""}, None), ({"type": "command", "command": "format_disk"}, None),
])
def test_bad_messages_rejected(server, sim, body, raw):
    code, r = post(server, body, raw=raw)
    assert r["success"] is False
    assert sim.fake.calls == []


def test_page_served(server):
    port = server._server.server_port
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
        assert r.status == 200 and b"PirateBox" in r.read()
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(f"http://127.0.0.1:{port}/../../etc/passwd", timeout=5)


def test_pair_page_loopback_only(engine):
    http = HttpInput(engine, host="0.0.0.0", port=0, token="secret-token")
    http.start()
    try:
        port = http.bound_port
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/pair", timeout=5) as r:
            page = r.read().decode()
        assert r.status == 200 and ("<svg" in page or "--lan" in page)
        uri = http.pairing_uri()
        if uri:  # machine has a LAN address
            assert uri.startswith("piratebox://pair?") and "token=secret-token" in uri
            lan = uri.split("host=")[1].split("&")[0]
            with pytest.raises(urllib.error.HTTPError) as e:  # same page via LAN address is refused
                urllib.request.urlopen(f"http://{lan}:{port}/pair", timeout=5)
            assert e.value.code == 403
    finally:
        http.stop()


def test_cors_only_for_configured_origin(engine):
    http = HttpInput(engine, host="127.0.0.1", port=0, token="t" * 20, allow_origin="http://localhost:8081")
    http.start()
    try:
        url = f"http://127.0.0.1:{http.bound_port}/api/message"
        for origin, allowed in (("http://localhost:8081", True), ("http://evil.example", False)):
            req = urllib.request.Request(url, data=b'{"type":"status"}', method="POST", headers={
                "Origin": origin, "Content-Type": "application/json", "X-PirateBox-Token": "t" * 20})
            with urllib.request.urlopen(req, timeout=5) as r:
                assert (r.headers.get("Access-Control-Allow-Origin") == origin) is allowed
    finally:
        http.stop()


def test_token_persists(tmp_path):
    from piratebox.pairing import load_or_create_token
    a = load_or_create_token(directory=tmp_path)
    assert load_or_create_token(directory=tmp_path) == a
    assert load_or_create_token(rotate=True, directory=tmp_path) != a
