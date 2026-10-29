"""
FLController: typed Python API over the FL link. One method per FL operation.

This is the only layer that knows opcodes. Everything above it (engine,
inputs, AI) deals in validated commands; everything below it is bytes.
"""

from ..errors import BridgeError, Status
from . import wire
from .link import FLLink

_FL_STATUS = {
    wire.ST_UNKNOWN_OPCODE: (Status.FL_REJECTED, "FL script does not know this operation (script out of date?)"),
    wire.ST_BAD_ARGS: (Status.FL_REJECTED, "FL script rejected the arguments"),
    wire.ST_EXEC_ERROR: (Status.FL_EXEC_ERROR, "FL script raised an error (see FL's Script output window)"),
    wire.ST_BAD_VERSION: (Status.FL_VERSION_MISMATCH, "FL script uses a different link protocol version"),
    wire.ST_NO_EFFECT: (Status.NO_EFFECT, "FL Studio had nothing to act on"),
}


class FLController:
    def __init__(self, link: FLLink):
        self.link = link

    def get_state(self) -> wire.FLState:
        return self._call(wire.OP_GET_STATE)

    def ping(self) -> wire.FLState:
        return self._call(wire.OP_PING)

    def play(self) -> wire.FLState:
        return self._call(wire.OP_PLAY)

    def stop(self) -> wire.FLState:
        return self._call(wire.OP_STOP)

    def record(self) -> wire.FLState:
        return self._call(wire.OP_RECORD)

    def go_to_start(self) -> wire.FLState:
        return self._call(wire.OP_GO_TO_START)

    def undo(self) -> wire.FLState:
        return self._call(wire.OP_UNDO, no_effect="Nothing to undo")

    def redo(self) -> wire.FLState:
        return self._call(wire.OP_REDO, no_effect="Nothing to redo")

    def save(self) -> wire.FLState:
        return self._call(wire.OP_SAVE)

    def set_metronome(self, enabled: bool) -> wire.FLState:
        return self._call(wire.OP_SET_METRONOME, [1 if enabled else 0])

    def show_window(self, window_id: int) -> wire.FLState:
        return self._call(wire.OP_SHOW_WINDOW, [window_id])

    def set_tempo(self, bpm: float) -> wire.FLState:
        return self._call(wire.OP_SET_TEMPO, wire.u21(round(bpm * 1000)))

    def move_bars(self, bars: int, forward: bool) -> wire.FLState:
        edge = "end" if forward else "start"
        return self._call(wire.OP_MOVE_BARS, [1 if forward else 0, bars],
                          no_effect=f"Already at the {edge} (or the song is empty)")

    def change_pattern(self, forward: bool) -> wire.FLState:
        edge = "last" if forward else "first"
        return self._call(wire.OP_CHANGE_PATTERN, [1 if forward else 0], no_effect=f"Already on the {edge} pattern")

    def clone_pattern(self) -> wire.FLState:
        return self._call(wire.OP_CLONE_PATTERN, no_effect="FL Studio did not create a copy")

    def set_track_mute(self, track: int, muted: bool) -> wire.FLState:
        return self._call(wire.OP_SET_TRACK_MUTE, [track, 1 if muted else 0])

    def set_track_solo(self, track: int, solo: bool) -> wire.FLState:
        return self._call(wire.OP_SET_TRACK_SOLO, [track, 1 if solo else 0])

    def select_track(self, track: int) -> wire.FLState:
        return self._call(wire.OP_SELECT_TRACK, [track])

    def nudge_track_volume(self, track: int, up: bool) -> wire.FLState:
        edge = "full volume" if up else "silent"
        return self._call(wire.OP_NUDGE_TRACK_VOLUME, [track, 1 if up else 0], no_effect=f"Track {track} is already {edge}")

    def quantize(self) -> wire.FLState:
        return self._call(wire.OP_QUANTIZE, no_effect="No channel is selected")

    def select_channel(self, position: int) -> wire.FLState:
        return self._call(wire.OP_SELECT_CHANNEL, wire.u21(position - 1),
                          bad_args=f"There is no channel {position} in the channel rack")

    def _call(self, op, payload=(), no_effect=None, bad_args=None) -> wire.FLState:
        frame = self.link.request(op, payload)
        if frame.version != wire.PROTOCOL_VERSION:
            raise BridgeError(Status.FL_VERSION_MISMATCH,
                              f"FL script speaks link v{frame.version}, bridge speaks v{wire.PROTOCOL_VERSION}")
        if not frame.payload:
            raise BridgeError(Status.FL_EXEC_ERROR, "empty response from FL script")
        status = frame.payload[0]
        if status != wire.ST_OK:
            code, msg = _FL_STATUS.get(status, (Status.FL_EXEC_ERROR, f"unknown FL status {status}"))
            if status == wire.ST_NO_EFFECT and no_effect:
                msg = no_effect
            elif status == wire.ST_BAD_ARGS and bad_args:
                msg = bad_args
            raise BridgeError(code, msg)
        return wire.decode_state(frame.payload[1:])
