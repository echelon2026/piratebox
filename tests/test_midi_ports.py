"""
Same pipeline, but over real OS MIDI ports (via mido/rtmidi) instead of the in-process pair.
Creates virtual ports, so it runs on macOS/Linux; on Windows it is skipped (use loopMIDI + real FL).
"""

import sys
import time

import pytest

mido = pytest.importorskip("mido")
pytest.importorskip("rtmidi")

if sys.platform == "win32":
    pytest.skip("rtmidi cannot create virtual ports on Windows", allow_module_level=True)

from piratebox.commands import CommandRegistry  # noqa: E402
from piratebox.engine import CommandEngine  # noqa: E402
from piratebox.fl.controller import FLController  # noqa: E402
from piratebox.fl.link import FLLink  # noqa: E402
from piratebox.fl.ports import MidoPortPair  # noqa: E402
from piratebox.fl.simulator import FakeFL, load_script  # noqa: E402
from piratebox.interpreters import RuleInterpreter  # noqa: E402
from piratebox.journal import NullJournal  # noqa: E402


class _Event:
    def __init__(self, data):
        self.sysex, self.handled = data, False


@pytest.fixture
def virtual_fl():
    """A fake 'FL Studio' that owns two virtual MIDI ports, like FL would with loopMIDI."""
    fake = FakeFL()
    script = load_script(fake)
    out = mido.open_output("MB Test FromFL", virtual=True)
    fake.output = lambda msg: out.send(mido.Message("sysex", data=list(msg)[1:-1]))
    inp = mido.open_input("MB Test ToFL", virtual=True,
                          callback=lambda m: m.type == "sysex" and script.OnSysEx(_Event(bytes(m.bytes()))))
    time.sleep(0.3)  # let CoreMIDI publish the ports
    yield fake
    inp.close()
    out.close()


def test_set_tempo_over_real_midi(virtual_fl):
    link = FLLink(MidoPortPair("MB Test ToFL", "MB Test FromFL"), timeout=2.0)
    engine = CommandEngine(FLController(link), CommandRegistry(), RuleInterpreter(), NullJournal())
    try:
        r = engine.handle_text("SET_TEMPO 142")
        assert r["success"], r
        assert virtual_fl.tempo_milli == 142000
        for cmd in ("PLAY", "STOP", "RECORD", "GO_TO_START", "STOP"):
            r = engine.handle_text(cmd)
            assert r["success"], r
    finally:
        link.close()


def test_missing_ports_reported():
    link = FLLink(MidoPortPair("No Such Port XYZ", "Nor This One"), timeout=0.5)
    engine = CommandEngine(FLController(link), CommandRegistry(), RuleInterpreter(), NullJournal())
    r = engine.handle_text("PLAY")
    assert not r["success"] and r["status"] == "FL_NOT_CONNECTED" and "not found" in r["message"]
