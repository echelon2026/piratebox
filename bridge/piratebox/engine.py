"""
CommandEngine: the single entry point for every input source.

    text  --interpreter-->  raw dict  --validate-->  Command  --controller-->  FL
                                                                  |
                                     result dict  <--verify state-+

It never raises: every outcome, including bugs, becomes a result dict with a
status code, and every step is journaled under one request_id.
"""

import time
import uuid
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from .commands import Command, CommandRegistry
from .errors import BridgeError, Status
from .fl import wire
from .fl.controller import FLController
from .fl.wire import FLState
from .interpreters import Interpreter
from .journal import Journal, NullJournal

TEMPO_TOLERANCE = 0.01
REVERIFY_DELAY = 0.15


@dataclass(frozen=True)
class Action:
    execute: Callable[[FLController, dict], FLState]
    verify: Optional[Callable[[dict, FLState], bool]]  # None = acknowledgement is the only evidence
    message: Callable[[dict], str]
    # Turns relative commands into absolute ones before executing, e.g. toggle -> set(on/off),
    # so the outcome can be verified. Receives the controller and the validated parameters.
    resolve: Optional[Callable[[FLController, dict], dict]] = None
    unconfirmed: str = "FL Studio acknowledged but its state did not change as expected"


def _track_name(p):
    return "Master" if p["track"] == 0 else f"Track {p['track']}"


def _bars(p):
    return f"{p['bars']} bar" + ("" if p["bars"] == 1 else "s")


def _on_off(p):
    return "on" if p["enabled"] else "off"


# The whitelist, in code. Must cover exactly the commands in protocol/commands.json (tested).
ACTIONS: Dict[str, Action] = {
    "set_tempo": Action(
        execute=lambda c, p: c.set_tempo(p["bpm"]),
        verify=lambda p, s: abs(s.tempo - p["bpm"]) < TEMPO_TOLERANCE,
        message=lambda p: f"Tempo set to {p['bpm']:g} BPM",
    ),
    "play": Action(lambda c, p: c.play(), lambda p, s: s.playing, lambda p: "Playing"),
    "stop": Action(lambda c, p: c.stop(), lambda p, s: not s.playing, lambda p: "Stopped"),
    "record": Action(lambda c, p: c.record(), lambda p, s: s.recording and s.playing, lambda p: "Recording"),
    "go_to_start": Action(lambda c, p: c.go_to_start(), None, lambda p: "Moved to start"),
    "undo": Action(lambda c, p: c.undo(), None, lambda p: "Undone"),
    "redo": Action(lambda c, p: c.redo(), None, lambda p: "Redone"),
    "save": Action(
        execute=lambda c, p: c.save(),
        verify=lambda p, s: not s.unsaved,
        message=lambda p: "Project saved",
        unconfirmed="Save did not complete. If the project has never been saved, "
                    "finish the Save As dialog on the FL Studio screen.",
    ),
    "set_metronome": Action(
        execute=lambda c, p: c.set_metronome(p["enabled"]),
        verify=lambda p, s: s.metronome == p["enabled"],
        message=lambda p: f"Metronome {_on_off(p)}",
    ),
    "toggle_metronome": Action(
        execute=lambda c, p: c.set_metronome(p["enabled"]),
        verify=lambda p, s: s.metronome == p["enabled"],
        message=lambda p: f"Metronome {_on_off(p)}",
        resolve=lambda c, p: {"enabled": not c.get_state().metronome},
    ),
    "open_mixer": Action(lambda c, p: c.show_window(wire.WID_MIXER), None, lambda p: "Mixer open"),
    "open_channel_rack": Action(lambda c, p: c.show_window(wire.WID_CHANNEL_RACK), None, lambda p: "Channel rack open"),
    "open_piano_roll": Action(lambda c, p: c.show_window(wire.WID_PIANO_ROLL), None, lambda p: "Piano roll open"),
    "next_pattern": Action(lambda c, p: c.change_pattern(True), None, lambda p: "Next pattern"),
    "previous_pattern": Action(lambda c, p: c.change_pattern(False), None, lambda p: "Previous pattern"),
    "duplicate_pattern": Action(lambda c, p: c.clone_pattern(), None, lambda p: "Pattern duplicated"),
    "mute_track": Action(lambda c, p: c.set_track_mute(p["track"], p["muted"]), None,
                         lambda p: f"{_track_name(p)} {'muted' if p['muted'] else 'unmuted'}"),
    "solo_track": Action(lambda c, p: c.set_track_solo(p["track"], p["solo"]), None,
                         lambda p: f"{_track_name(p)} {'soloed' if p['solo'] else 'unsoloed'}"),
    "select_track": Action(lambda c, p: c.select_track(p["track"]), None, lambda p: f"{_track_name(p)} selected"),
    "volume_up": Action(lambda c, p: c.nudge_track_volume(p["track"], True), None, lambda p: f"{_track_name(p)} up"),
    "volume_down": Action(lambda c, p: c.nudge_track_volume(p["track"], False), None, lambda p: f"{_track_name(p)} down"),
    "go_back_bars": Action(lambda c, p: c.move_bars(p["bars"], False), None, lambda p: f"Back {_bars(p)}"),
    "go_forward_bars": Action(lambda c, p: c.move_bars(p["bars"], True), None, lambda p: f"Forward {_bars(p)}"),
    "quantize": Action(lambda c, p: c.quantize(), None, lambda p: "Quantized the selected channel"),
    "select_channel": Action(lambda c, p: c.select_channel(p["channel"]), None, lambda p: f"Channel {p['channel']} selected"),
    "get_status": Action(lambda c, p: c.ping(), None, lambda p: "FL Studio connected"),
}


class CommandEngine:
    def __init__(self, controller: FLController, registry: CommandRegistry,
                 interpreter: Interpreter, journal: Journal = None):
        self.controller = controller
        self.registry = registry
        self.interpreter = interpreter
        self.journal = journal or NullJournal()
        self._last_probe_connected = None
        self._last_state = None  # last FL state seen; gives the AI context ("10 BPM faster")

    def handle_text(self, text: str, source: str = "unknown") -> dict:
        rid = _new_id()
        self.journal.record(rid, "input", source=source, text=text)
        try:
            interp = self.interpreter.interpret(text, self._last_state)
        except BridgeError as e:
            return self._fail(rid, None, None, e)
        except Exception as e:
            return self._fail(rid, None, None, BridgeError(Status.INTERNAL_ERROR, f"interpreter failed: {e!r}"))
        self.journal.record(rid, "interpreted", interpreter=interp.source, command=interp.command,
                            clarification=interp.clarification, **interp.details)
        if interp.clarification:
            result = self._fail(rid, None, None, BridgeError(Status.NEEDS_CLARIFICATION, interp.clarification))
            return {**result, "requires_clarification": True, "interpreter": interp.source}
        if interp.command is None:
            return self._fail(rid, None, None, BridgeError(
                Status.PARSE_FAILED,
                f"Couldn't understand '{text}'. Try an exact command like 'set tempo 142' or 'play'."))
        return {**self._run(rid, interp.command), "interpreter": interp.source}

    def probe(self) -> dict:
        """Connectivity check for UIs that poll. Journals only when connectivity changes."""
        try:
            state = self.controller.get_state()
            result = {"connected": True, "status": Status.OK.value, "state": state.to_dict()}
            self._last_state = result["state"]
        except BridgeError as e:
            result = {"connected": False, "status": e.status.value, "message": e.message}
        except Exception as e:
            result = {"connected": False, "status": Status.INTERNAL_ERROR.value, "message": repr(e)}
        if result["connected"] != self._last_probe_connected:
            self._last_probe_connected = result["connected"]
            self.journal.record(_new_id(), "connectivity", **{k: v for k, v in result.items() if k != "state"})
        return result

    def handle_command(self, raw, source: str = "unknown") -> dict:
        rid = _new_id()
        self.journal.record(rid, "input", source=source, command=raw)
        return self._run(rid, raw)

    def _run(self, rid, raw) -> dict:
        cmd = None
        try:
            cmd = self.registry.validate(raw)
            self.journal.record(rid, "validated", command=cmd.to_dict())

            if self.registry.spec(cmd.name).get("requires_confirmation") and not cmd.confirmed:
                raise BridgeError(Status.CONFIRMATION_REQUIRED,
                                  f"'{cmd.name}' needs confirmation: resend with \"confirmed\": true")

            action = ACTIONS.get(cmd.name)
            if action is None:  # schema lists it but code does not implement it
                raise BridgeError(Status.UNKNOWN_COMMAND, f"'{cmd.name}' is not implemented")

            params = cmd.parameters
            if action.resolve:
                params = action.resolve(self.controller, params)
                self.journal.record(rid, "resolved", parameters=params)

            self.journal.record(rid, "sent", command=cmd.name)
            state = action.execute(self.controller, params)
            self.journal.record(rid, "acknowledged", state=state.to_dict())

            if action.verify and not action.verify(params, state):
                # FL may apply some changes a tick later; look once more before failing.
                time.sleep(REVERIFY_DELAY)
                state = self.controller.get_state()
                self.journal.record(rid, "reverified", state=state.to_dict())
                if not action.verify(params, state):
                    raise _NotConfirmed(state, action.unconfirmed)

            return self._finish(rid, cmd, {
                "success": True, "status": Status.OK.value,
                "message": action.message(params), "state": state.to_dict(),
            })
        except _NotConfirmed as e:
            return self._fail(rid, cmd, raw, BridgeError(Status.NOT_CONFIRMED, e.message), state=e.state)
        except BridgeError as e:
            return self._fail(rid, cmd, raw, e)
        except Exception as e:
            return self._fail(rid, cmd, raw, BridgeError(Status.INTERNAL_ERROR, repr(e)))

    def _fail(self, rid, cmd: Optional[Command], raw, error: BridgeError, state: FLState = None) -> dict:
        result = {"success": False, "status": error.status.value, "message": error.message}
        if state is not None:
            result["state"] = state.to_dict()
        if cmd is None and isinstance(raw, dict) and isinstance(raw.get("command"), str):
            result["command"] = raw["command"]
        return self._finish(rid, cmd, result)

    def _finish(self, rid, cmd: Optional[Command], result: dict) -> dict:
        if "state" in result:
            self._last_state = result["state"]
        out = {"request_id": (cmd.request_id if cmd and cmd.request_id else rid), **result}
        if cmd is not None:
            out["command"] = cmd.name
            out["parameters"] = dict(cmd.parameters)
        self.journal.record(rid, "result", **{k: v for k, v in out.items() if k != "request_id"})
        return out


class _NotConfirmed(Exception):
    def __init__(self, state, message):
        self.state = state
        self.message = message


def _new_id():
    return uuid.uuid4().hex[:8]
