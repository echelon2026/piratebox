"""
SysEx wire format shared with flstudio-controller/PirateBox/device_PirateBox.py.

    F0 7D 4D 42 <version> <msg_type> <seq> <opcode> <payload...> F7

mido strips F0/F7, so everything here works on the inner data bytes.
All bytes are 7-bit. Numbers larger than 127 use u21 (three 7-bit bytes, big endian).
"""

from dataclasses import dataclass

PROTOCOL_VERSION = 1
MANUFACTURER_ID = 0x7D
MAGIC = (0x4D, 0x42)

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

WID_MIXER = 0
WID_CHANNEL_RACK = 1
WID_PLAYLIST = 2
WID_PIANO_ROLL = 3
WID_BROWSER = 4

EV_HELLO = 0x01

ST_OK = 0x00
ST_UNKNOWN_OPCODE = 0x01
ST_BAD_ARGS = 0x02
ST_EXEC_ERROR = 0x03
ST_BAD_VERSION = 0x04
ST_NO_EFFECT = 0x05

FLAG_PLAYING = 0x01
FLAG_RECORDING = 0x02
FLAG_METRONOME = 0x04
FLAG_UNSAVED = 0x08

U21_MAX = 0x1FFFFF


class WireError(ValueError):
    pass


@dataclass(frozen=True)
class Frame:
    version: int
    msg_type: int
    seq: int
    op: int
    payload: tuple


@dataclass(frozen=True)
class FLState:
    tempo: float
    playing: bool
    recording: bool
    metronome: bool
    unsaved: bool = False

    def to_dict(self):
        return {
            "tempo": self.tempo,
            "playing": self.playing,
            "recording": self.recording,
            "metronome": self.metronome,
            "unsaved_changes": self.unsaved,
        }


def u21(value):
    if not 0 <= value <= U21_MAX:
        raise WireError(f"value {value} does not fit in u21")
    return [(value >> 14) & 0x7F, (value >> 7) & 0x7F, value & 0x7F]


def from_u21(data):
    return (data[0] << 14) | (data[1] << 7) | data[2]


def encode(msg_type, seq, op, payload=()):
    data = [MANUFACTURER_ID, *MAGIC, PROTOCOL_VERSION, msg_type, seq, op, *payload]
    if any(not 0 <= b <= 0x7F for b in data):
        raise WireError("sysex data bytes must be 7-bit")
    return data


def decode(data):
    data = list(data)
    if data and data[0] == 0xF0:
        data = data[1:]
    if data and data[-1] == 0xF7:
        data = data[:-1]
    if len(data) < 7 or data[0] != MANUFACTURER_ID or tuple(data[1:3]) != MAGIC:
        raise WireError("not a PirateBox frame")
    return Frame(version=data[3], msg_type=data[4], seq=data[5], op=data[6], payload=tuple(data[7:]))


def decode_state(payload):
    if len(payload) < 4:
        raise WireError("state payload too short")
    return FLState(
        tempo=from_u21(payload[0:3]) / 1000.0,
        playing=bool(payload[3] & FLAG_PLAYING),
        recording=bool(payload[3] & FLAG_RECORDING),
        metronome=bool(payload[3] & FLAG_METRONOME),
        unsaved=bool(payload[3] & FLAG_UNSAVED),
    )
