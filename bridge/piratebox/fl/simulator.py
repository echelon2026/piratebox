"""
In-process FL Studio simulator.

Loads the *real* flstudio-controller script with fake versions of FL's API
modules (channels, device, general, midi, mixer, patterns, transport, ui), and exposes a PortPair
that routes SysEx straight into the script's OnSysEx. Used by the test suite
and by `piratebox --simulate`, so the whole pipeline can be exercised on a
machine without FL Studio.

The fakes model only the behaviour the script relies on, including FL's
toggle semantics (transport.start() is play/pause, transport.record() toggles).
"""

import importlib.util
import threading
import types
from pathlib import Path
from unittest import mock

DEFAULT_SCRIPT = (
    Path(__file__).resolve().parents[3] / "flstudio-controller" / "PirateBox" / "device_PirateBox.py"
)


class FakeFL:
    REC_Tempo = 0x5000  # arbitrary; the script only uses the symbols
    REC_Control = 0x10000
    REC_UpdateControl = 0x40000
    FPT_Save = 92
    FPT_Metronome = 110
    SONGLENGTH_BARS = 3

    def __init__(self):
        self.tempo_milli = 140000
        self.playing = False
        self.recording = False
        self.metronome = False
        self.song_pos = 0.37
        self.api_version = 36
        self.undo_length = 3  # history entries
        self.undo_last = 0  # FL: 0 = most recent, higher = further back
        self.changed = 1  # FL getChangedFlag(): 0 saved, 1 unsaved
        self.untitled = False  # True: Save opens a dialog instead of saving
        self.windows_visible = set()
        self.focused = None
        self.responsive = True  # False simulates FL hung / script inactive
        self.ignore_tempo = False  # True simulates FL acking but not changing tempo
        self.song_bars = 16  # song length in bars; 0 = empty song
        self.pattern = 1  # FL patterns are 1-based
        self.pattern_count = 3
        self.can_clone = True
        self.track_muted = set()
        self.track_solo = set()
        self.track_volume = {}  # track -> fader 0..1; default 0.8 (0 dB)
        self.selected_track = 0
        self.channel_count = 4
        self.selected_channel = 0  # 0-based; -1 = none
        self.quantized = []
        self.calls = []
        self.output = None  # set by SimulatedPortPair

    def modules(self):
        fl = self
        device = types.ModuleType("device")
        device.midiOutSysex = lambda msg: fl.output and fl.output(bytes(msg))

        general = types.ModuleType("general")
        general.getVersion = lambda: fl.api_version

        def process_rec_event(target, value, flags):
            fl.calls.append(("processRECEvent", target, value, flags))
            if target == fl.REC_Tempo and flags & fl.REC_Control and not fl.ignore_tempo:
                fl.tempo_milli = value
            return value
        general.processRECEvent = process_rec_event
        general.getChangedFlag = lambda: fl.changed
        general.getUndoHistoryLast = lambda: fl.undo_last

        def undo_up():
            fl.calls.append(("undoUp",))
            if fl.undo_last < fl.undo_length - 1:
                fl.undo_last += 1
            return 0

        def undo_down():
            fl.calls.append(("undoDown",))
            if fl.undo_last > 0:
                fl.undo_last -= 1
            return 0
        general.undoUp, general.undoDown = undo_up, undo_down

        midi = types.ModuleType("midi")
        midi.REC_Tempo, midi.REC_Control, midi.REC_UpdateControl = fl.REC_Tempo, fl.REC_Control, fl.REC_UpdateControl
        midi.FPT_Save, midi.FPT_Metronome = fl.FPT_Save, fl.FPT_Metronome
        midi.SONGLENGTH_BARS = fl.SONGLENGTH_BARS

        mixer = types.ModuleType("mixer")
        mixer.getCurrentTempo = lambda asInt=False: fl.tempo_milli if asInt else fl.tempo_milli / 1000
        mixer.isTrackMuted = lambda t: t in fl.track_muted
        mixer.isTrackSolo = lambda t: t in fl.track_solo
        mixer.getTrackVolume = lambda t: fl.track_volume.get(t, 0.8)

        def toggle(collection, name):
            def run(t):
                fl.calls.append((name, t))
                collection.symmetric_difference_update({t})
            return run
        mixer.muteTrack = toggle(fl.track_muted, "muteTrack")
        mixer.soloTrack = toggle(fl.track_solo, "soloTrack")

        def set_track_volume(t, value):
            fl.calls.append(("setTrackVolume", t, value))
            fl.track_volume[t] = value
        mixer.setTrackVolume = set_track_volume

        def set_track_number(t):
            fl.calls.append(("setTrackNumber", t))
            fl.selected_track = t
        mixer.setTrackNumber = set_track_number

        patterns = types.ModuleType("patterns")
        patterns.patternNumber = lambda: fl.pattern
        patterns.patternCount = lambda: fl.pattern_count

        def jump_to_pattern(n):
            fl.calls.append(("jumpToPattern", n))
            fl.pattern = n
        patterns.jumpToPattern = jump_to_pattern

        def clone_pattern(n):
            fl.calls.append(("clonePattern", n))
            if fl.can_clone:
                fl.pattern_count += 1
                fl.pattern = fl.pattern_count
        patterns.clonePattern = clone_pattern

        channels = types.ModuleType("channels")
        channels.channelCount = lambda: fl.channel_count
        channels.selectedChannel = lambda: fl.selected_channel

        def select_one_channel(i):
            fl.calls.append(("selectOneChannel", i))
            fl.selected_channel = i
        channels.selectOneChannel = select_one_channel

        def quick_quantize(i, startOnly=1):
            fl.calls.append(("quickQuantize", i))
            fl.quantized.append(i)
        channels.quickQuantize = quick_quantize

        transport = types.ModuleType("transport")

        def start():
            fl.calls.append(("start",))
            fl.playing = not fl.playing

        def stop():
            fl.calls.append(("stop",))
            fl.playing = False
            fl.song_pos = 0.0

        def record():
            fl.calls.append(("record",))
            fl.recording = not fl.recording

        def set_song_pos(pos, mode=-1):
            fl.calls.append(("setSongPos", pos, mode))
            fl.song_pos = pos

        transport.start, transport.stop, transport.record = start, stop, record
        transport.setSongPos = set_song_pos
        transport.getSongPos = lambda mode=-1: fl.song_pos
        transport.getSongLength = lambda mode: fl.song_bars
        transport.isPlaying = lambda: fl.playing
        transport.isRecording = lambda: fl.recording

        def global_transport(command, value, pmeflags=None, flags=None):
            fl.calls.append(("globalTransport", command, value))
            if command == fl.FPT_Save and not fl.untitled:
                fl.changed = 0
            elif command == fl.FPT_Metronome:
                fl.metronome = not fl.metronome
            return 1
        transport.globalTransport = global_transport

        ui = types.ModuleType("ui")
        ui.isMetronomeEnabled = lambda: fl.metronome
        ui.showWindow = lambda wid: fl.windows_visible.add(wid)
        ui.getVisible = lambda wid: wid in fl.windows_visible

        def set_focused(wid):
            fl.focused = wid
        ui.setFocused = set_focused

        return {"channels": channels, "device": device, "general": general, "midi": midi, "mixer": mixer,
                "patterns": patterns, "transport": transport, "ui": ui}


class _SysExEvent:
    def __init__(self, data: bytes):
        self.sysex = data
        self.handled = False


def load_script(fake: FakeFL, script_path: Path = DEFAULT_SCRIPT):
    spec = importlib.util.spec_from_file_location("device_PirateBox_sim", script_path)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict("sys.modules", fake.modules()):
        spec.loader.exec_module(module)
    return module


class SimulatedPortPair:
    """PortPair wired directly to the FL script. FL runs scripts on its own
    thread, so requests are delivered on a worker thread here as well."""

    def __init__(self, fake: FakeFL = None, script_path: Path = DEFAULT_SCRIPT):
        self.fake = fake or FakeFL()
        self.script = load_script(self.fake, script_path)
        self.is_open = False
        self._on_receive = None
        self._fl_thread_lock = threading.Lock()

    def open(self, on_receive) -> None:
        self._on_receive = on_receive
        self.fake.output = lambda msg: self._on_receive and self._on_receive(list(msg))
        self.is_open = True
        self.script.OnInit()

    def send(self, data) -> None:
        if not self.fake.responsive:
            return
        event = _SysExEvent(bytes([0xF0, *data, 0xF7]))

        def run():
            with self._fl_thread_lock:
                self.script.OnSysEx(event)
        threading.Thread(target=run, daemon=True).start()

    def close(self) -> None:
        self.is_open = False
        self.fake.output = None
