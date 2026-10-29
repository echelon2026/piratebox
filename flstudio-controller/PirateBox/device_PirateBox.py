# name=PirateBox Controller
# url=https://github.com/
"""
FL Studio side of PirateBox.

Runs inside FL Studio's embedded Python as a MIDI controller script. It
receives SysEx requests from the PirateBox application over a virtual MIDI
port, executes them through FL Studio's official scripting API, and answers
with a SysEx response carrying a status code and a snapshot of FL's state.

Deliberately dependency-free (FL's Python does not guarantee the standard
library), and deliberately dumb: it only knows a fixed opcode table. All
validation of user intent happens in the bridge; this script re-checks ranges
as a second line of defence and never executes anything not in HANDLERS.

Wire format (must match bridge/piratebox/fl/wire.py, see docs/ARCHITECTURE.md):
    F0 7D 4D 42 <version> <msg_type> <seq> <opcode> <payload...> F7
"""

import channels
import device
import general
import midi
import mixer
import patterns
import transport
import ui

PROTOCOL_VERSION = 1
MANUFACTURER_ID = 0x7D  # "non-commercial / educational" ID, never a real vendor
MAGIC = (0x4D, 0x42)  # "MB"

MT_REQUEST = 0x01
MT_RESPONSE = 0x02
MT_EVENT = 0x03

OP_PING = 0x00
OP_GET_STATE = 0x01
OP_PLAY = 0x10
OP_STOP = 0x11
OP_RECORD = 0x12
OP_GO_TO_START = 0x13
OP_UNDO = 0x14
OP_REDO = 0x15
OP_SAVE = 0x16
OP_SET_METRONOME = 0x17
OP_SHOW_WINDOW = 0x18
OP_SET_TEMPO = 0x20
OP_MOVE_BARS = 0x21
OP_CHANGE_PATTERN = 0x30
OP_CLONE_PATTERN = 0x31
OP_SET_TRACK_MUTE = 0x40
OP_SET_TRACK_SOLO = 0x41
OP_SELECT_TRACK = 0x42
OP_NUDGE_TRACK_VOLUME = 0x43
OP_QUANTIZE = 0x50
OP_SELECT_CHANNEL = 0x51

EV_HELLO = 0x01

ST_OK = 0x00
ST_UNKNOWN_OPCODE = 0x01
ST_BAD_ARGS = 0x02
ST_EXEC_ERROR = 0x03
ST_BAD_VERSION = 0x04
ST_NO_EFFECT = 0x05

# FL Studio's own tempo limits, in milli-BPM.
TEMPO_MIN_MILLI = 10000
TEMPO_MAX_MILLI = 522000

FLAG_PLAYING = 0x01
FLAG_RECORDING = 0x02
FLAG_METRONOME = 0x04
FLAG_UNSAVED = 0x08

MAX_MIXER_TRACK = 125  # 0 = master, 1-125 = inserts
VOLUME_STEP = 0.05  # fader units (0.8 = 0 dB); roughly 1.5 dB near unity
MAX_BARS = 64

# Windows the bridge may open: mixer, channel rack, playlist, piano roll, browser.
ALLOWED_WINDOWS = (0, 1, 2, 3, 4)


class _BadArgs(Exception):
    pass


class _NoEffect(Exception):
    pass


def _log(text):
    # Shows in FL Studio's View > Script output window.
    print("[PirateBox] " + text)


def _u21(value):
    return [(value >> 14) & 0x7F, (value >> 7) & 0x7F, value & 0x7F]


def _from_u21(data):
    return (data[0] << 14) | (data[1] << 7) | data[2]


def _state_bytes():
    try:
        tempo = int(mixer.getCurrentTempo(True))
    except Exception:
        tempo = 0
    flags = 0
    if transport.isPlaying():
        flags |= FLAG_PLAYING
    if transport.isRecording():
        flags |= FLAG_RECORDING
    try:
        if ui.isMetronomeEnabled():
            flags |= FLAG_METRONOME
    except Exception:
        pass
    try:
        if general.getChangedFlag():
            flags |= FLAG_UNSAVED
    except Exception:
        pass
    return _u21(max(0, min(tempo, 0x1FFFFF))) + [flags]


def _send(msg_type, seq, op, payload):
    body = [MANUFACTURER_ID, MAGIC[0], MAGIC[1], PROTOCOL_VERSION, msg_type, seq & 0x7F, op & 0x7F]
    body += [b & 0x7F for b in payload]
    device.midiOutSysex(bytes([0xF0] + body + [0xF7]))


def _respond(seq, op, status):
    _send(MT_RESPONSE, seq, op, [status] + _state_bytes())


# ---- command handlers -------------------------------------------------------

def _no_args(args):
    if args:
        raise _BadArgs()


def _ping(args):
    pass


def _play(args):
    _no_args(args)
    if not transport.isPlaying():
        transport.start()  # start() toggles play/pause, so only call when stopped


def _stop(args):
    _no_args(args)
    transport.stop()


def _record(args):
    _no_args(args)
    if not transport.isRecording():
        transport.record()  # record() toggles, so only call when not armed
    if not transport.isPlaying():
        transport.start()


def _go_to_start(args):
    _no_args(args)
    transport.setSongPos(0.0)


def _undo_step(step):
    try:
        before = general.getUndoHistoryLast()
    except Exception:
        before = None
    step()
    if before is not None and general.getUndoHistoryLast() == before:
        raise _NoEffect()


def _undo(args):
    _no_args(args)
    _undo_step(general.undoUp)  # "up" = undo (FL 21+ reversed only the visual order)


def _redo(args):
    _no_args(args)
    _undo_step(general.undoDown)


def _save(args):
    _no_args(args)
    transport.globalTransport(midi.FPT_Save, 1)


def _set_metronome(args):
    if len(args) != 1 or args[0] not in (0, 1):
        raise _BadArgs()
    if bool(ui.isMetronomeEnabled()) != bool(args[0]):
        transport.globalTransport(midi.FPT_Metronome, 1)  # FPT_Metronome toggles


def _show_window(args):
    if len(args) != 1 or args[0] not in ALLOWED_WINDOWS:
        raise _BadArgs()
    ui.showWindow(args[0])
    ui.setFocused(args[0])


def _set_tempo(args):
    if len(args) != 3:
        raise _BadArgs()
    milli = _from_u21(args)
    if milli < TEMPO_MIN_MILLI or milli > TEMPO_MAX_MILLI:
        raise _BadArgs()
    general.processRECEvent(midi.REC_Tempo, milli, midi.REC_Control | midi.REC_UpdateControl)


def _flag(value):
    if value not in (0, 1):
        raise _BadArgs()
    return value == 1


def _track(value):
    if value > MAX_MIXER_TRACK:
        raise _BadArgs()
    return value


def _move_bars(args):
    if len(args) != 2 or not 1 <= args[1] <= MAX_BARS:
        raise _BadArgs()
    forward, bars = _flag(args[0]), args[1]
    total = transport.getSongLength(midi.SONGLENGTH_BARS)
    if total <= 0:
        raise _NoEffect()
    before = transport.getSongPos()
    step = float(bars) / total
    after = min(1.0, before + step) if forward else max(0.0, before - step)
    if after == before:
        raise _NoEffect()
    transport.setSongPos(after)


def _change_pattern(args):
    if len(args) != 1:
        raise _BadArgs()
    current = patterns.patternNumber()
    target = current + 1 if _flag(args[0]) else current - 1
    if target < 1 or target > patterns.patternCount():
        raise _NoEffect()
    patterns.jumpToPattern(target)


def _clone_pattern(args):
    _no_args(args)
    before = patterns.patternCount()
    patterns.clonePattern(patterns.patternNumber())
    if patterns.patternCount() == before:
        raise _NoEffect()


def _set_track_mute(args):
    if len(args) != 2:
        raise _BadArgs()
    track, want = _track(args[0]), _flag(args[1])
    if bool(mixer.isTrackMuted(track)) != want:
        mixer.muteTrack(track)  # toggles
    if bool(mixer.isTrackMuted(track)) != want:
        raise Exception("mute state did not change")


def _set_track_solo(args):
    if len(args) != 2:
        raise _BadArgs()
    track, want = _track(args[0]), _flag(args[1])
    if bool(mixer.isTrackSolo(track)) != want:
        mixer.soloTrack(track)  # toggles
    if bool(mixer.isTrackSolo(track)) != want:
        raise Exception("solo state did not change")


def _select_track(args):
    if len(args) != 1:
        raise _BadArgs()
    mixer.setTrackNumber(_track(args[0]))


def _nudge_track_volume(args):
    if len(args) != 2:
        raise _BadArgs()
    track, up = _track(args[0]), _flag(args[1])
    before = mixer.getTrackVolume(track)
    after = min(1.0, before + VOLUME_STEP) if up else max(0.0, before - VOLUME_STEP)
    if after == before:
        raise _NoEffect()
    mixer.setTrackVolume(track, after)


def _quantize(args):
    _no_args(args)
    index = channels.selectedChannel()
    if index < 0:
        raise _NoEffect()
    channels.quickQuantize(index)


def _select_channel(args):
    if len(args) != 3:
        raise _BadArgs()
    index = _from_u21(args)
    if index >= channels.channelCount():
        raise _BadArgs()
    channels.selectOneChannel(index)


HANDLERS = {
    OP_PING: _ping,
    OP_GET_STATE: _ping,
    OP_PLAY: _play,
    OP_STOP: _stop,
    OP_RECORD: _record,
    OP_GO_TO_START: _go_to_start,
    OP_UNDO: _undo,
    OP_REDO: _redo,
    OP_SAVE: _save,
    OP_SET_METRONOME: _set_metronome,
    OP_SHOW_WINDOW: _show_window,
    OP_SET_TEMPO: _set_tempo,
    OP_MOVE_BARS: _move_bars,
    OP_CHANGE_PATTERN: _change_pattern,
    OP_CLONE_PATTERN: _clone_pattern,
    OP_SET_TRACK_MUTE: _set_track_mute,
    OP_SET_TRACK_SOLO: _set_track_solo,
    OP_SELECT_TRACK: _select_track,
    OP_NUDGE_TRACK_VOLUME: _nudge_track_volume,
    OP_QUANTIZE: _quantize,
    OP_SELECT_CHANNEL: _select_channel,
}


# ---- FL Studio callbacks ----------------------------------------------------

def OnInit():
    _log("controller script loaded (link protocol v%d)" % PROTOCOL_VERSION)
    try:
        api = int(general.getVersion())
    except Exception:
        api = 0
    try:
        _send(MT_EVENT, 0, EV_HELLO, _u21(api) + _state_bytes())
    except Exception as e:
        # Output port may not be linked yet; the bridge pings on connect anyway.
        _log("could not send hello: %r" % (e,))


def OnDeInit():
    _log("controller script unloaded")


def OnSysEx(event):
    data = list(event.sysex or b"")
    if data and data[0] == 0xF0:
        data = data[1:]
    if data and data[-1] == 0xF7:
        data = data[:-1]
    if len(data) < 7 or data[0] != MANUFACTURER_ID or (data[1], data[2]) != MAGIC:
        return  # not ours - leave it for FL to handle normally

    event.handled = True
    version, msg_type, seq, op, args = data[3], data[4], data[5], data[6], data[7:]
    if msg_type != MT_REQUEST:
        return
    if version != PROTOCOL_VERSION:
        _log("rejected request with protocol v%d" % version)
        _respond(seq, op, ST_BAD_VERSION)
        return

    handler = HANDLERS.get(op)
    if handler is None:
        _log("unknown opcode 0x%02X" % op)
        status = ST_UNKNOWN_OPCODE
    else:
        try:
            handler(args)
            status = ST_OK
        except _BadArgs:
            _log("bad arguments for opcode 0x%02X" % op)
            status = ST_BAD_ARGS
        except _NoEffect:
            status = ST_NO_EFFECT
        except Exception as e:
            _log("error executing opcode 0x%02X: %r" % (op, e))
            status = ST_EXEC_ERROR
    _respond(seq, op, status)
