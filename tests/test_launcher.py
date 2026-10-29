"""Launcher step logic with a fake machine (the real one needs Windows, loopMIDI and FL Studio)."""

import pytest

from piratebox.launcher import Launcher, Settings, System


class FakeProc:
    def __init__(self):
        self.exit_code = None

    def poll(self):
        return self.exit_code


class FakeSystem(System):
    def __init__(self):
        self.ports = ["PirateBox ToFL 1", "PirateBox FromFL 2"]
        self.running = set()
        self.files = {r"C:\Program Files\Image-Line\FL Studio 2025\FL64.exe"}
        self.started = []
        self.bridge_up = False
        self.fl_connected_after = 0  # status polls before FL answers
        self.polls = 0
        self.clock = 0.0
        self.ports_after_loopmidi = None

    def midi_ports(self):
        return self.ports

    def process_running(self, exe):
        return exe in self.running

    def find_file(self, patterns):
        return sorted(self.files)[0] if self.files else None

    def exists(self, path):
        return path in self.files or path.endswith("loopMIDI.exe")

    def start(self, argv, cwd=None):
        self.started.append(argv)
        if "piratebox" in argv:
            self.bridge_up = True
        elif argv[0].endswith("loopMIDI.exe") and self.ports_after_loopmidi is not None:
            self.ports = self.ports_after_loopmidi
        return FakeProc()

    def status(self, port, token):
        if not self.bridge_up:
            return None
        self.polls += 1
        if self.polls > self.fl_connected_after:
            return {"connected": True, "status": "OK", "state": {"tempo": 140.0}}
        return {"connected": False, "status": "FL_TIMEOUT", "message": "FL Studio did not answer"}

    def sleep(self, seconds):
        self.clock += seconds

    def now(self):
        return self.clock


def run(system, **settings):
    events = []
    launcher = Launcher(Settings(**settings), lambda *e: events.append(e), system, python="py", token="t" * 20)
    return launcher.run(), events, launcher


def finals(events):
    return [(step, state) for step, state, _ in events if state != "run"]


def test_happy_path_starts_bridge_then_fl():
    system = FakeSystem()
    system.fl_connected_after = 3
    ok, events, _ = run(system)
    assert ok
    assert finals(events) == [("MIDI link", "ok"), ("PirateBox", "ok"), ("FL Studio", "ok"), ("Ready", "ok")]
    bridge, fl = system.started
    assert bridge[:3] == ["py", "-m", "piratebox"] and "--lan" in bridge and "--simulate" not in bridge
    assert bridge[bridge.index("--token") + 1] == "t" * 20
    assert fl == [r"C:\Program Files\Image-Line\FL Studio 2025\FL64.exe"]
    assert events[-1][2] == "tempo 140 BPM"


def test_fl_already_open_is_not_launched_twice():
    system = FakeSystem()
    system.running = {"FL64.exe"}
    ok, events, _ = run(system)
    assert ok and len(system.started) == 1  # only the bridge
    assert ("FL Studio", "ok", "already open") in events


def test_missing_ports_starts_loopmidi():
    system = FakeSystem()
    system.ports = []
    system.ports_after_loopmidi = ["PirateBox ToFL", "PirateBox FromFL"]
    ok, events, _ = run(system)
    assert ok and system.started[0][0].endswith("loopMIDI.exe")
    assert ("MIDI link", "ok", "started loopMIDI") in events


def test_missing_ports_fails_with_hint():
    system = FakeSystem()
    system.ports = ["Some Other Port"]
    ok, events, _ = run(system)
    assert not ok
    step, state, detail = events[-1]
    assert (step, state) == ("MIDI link", "fail") and "loopMIDI" in detail
    assert not any("piratebox" in a for a in system.started)  # never got as far as the bridge


def test_fl_not_installed():
    system = FakeSystem()
    system.files = set()
    ok, events, _ = run(system)
    assert not ok and events[-1][:2] == ("FL Studio", "fail")


def test_fl_never_answers_times_out():
    system = FakeSystem()
    system.fl_connected_after = 10 ** 6
    ok, events, _ = run(system, fl_timeout=30)
    assert not ok
    step, state, detail = events[-1]
    assert (step, state) == ("Ready", "fail") and "MIDI settings" in detail and "did not answer" in detail
    assert system.clock >= 30


def test_simulate_skips_hardware_steps():
    system = FakeSystem()
    system.ports = []
    ok, events, _ = run(system, simulate=True)
    assert ok
    assert finals(events) == [("MIDI link", "skip"), ("PirateBox", "ok"), ("FL Studio", "skip"), ("Ready", "ok")]
    assert "--simulate" in system.started[0]


def test_bridge_already_running_is_reused():
    system = FakeSystem()
    system.bridge_up = True
    ok, events, _ = run(system, simulate=True)
    assert ok and system.started == []
    assert ("PirateBox", "ok", "already running") in events


def test_supervise_restarts_a_crashed_bridge():
    system = FakeSystem()
    ok, _, launcher = run(system, simulate=True)
    assert ok
    launcher.bridge.exit_code = 1
    system.bridge_up = False
    ticks = iter([False, True])
    launcher.supervise(stop=lambda: next(ticks))
    assert len(system.started) == 2 and system.bridge_up


@pytest.mark.parametrize("names", [["PirateBox ToFL"], ["PirateBox FromFL 1"]])
def test_both_ports_required(names):
    system = FakeSystem()
    system.ports = names
    assert not run(system)[0]
