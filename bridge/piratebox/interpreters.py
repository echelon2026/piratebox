"""
Interpreters turn free text into a raw command dict, which the engine then validates.

- RuleInterpreter: exact phrases and simple patterns. Instant, free, deterministic.
- AIInterpreter (ai/): conversational language, via a language model constrained to our vocabulary.
- ChainInterpreter: tries each in order. The default is rules first, then AI.

Whatever an interpreter returns goes through exactly the same validation as any other input.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Optional, Protocol


@dataclass
class Interpretation:
    command: Optional[dict] = None  # raw command for validation; None = not understood
    clarification: Optional[str] = None  # set when the user should rephrase/answer a question
    source: str = "rules"
    details: dict = field(default_factory=dict)  # journaled: model, latency, token usage...


class Interpreter(Protocol):
    def interpret(self, text: str, context: Optional[dict] = None) -> Interpretation:
        """context: last known FL state (may be stale), e.g. {"tempo": 140.0, ...}."""


class ChainInterpreter:
    def __init__(self, *interpreters):
        self.interpreters = interpreters

    def interpret(self, text: str, context: Optional[dict] = None) -> Interpretation:
        result = Interpretation()
        for interpreter in self.interpreters:
            result = interpreter.interpret(text, context)
            if result.command is not None or result.clarification:
                return result
        return result


_NUM = r"(\d+(?:\.\d+)?)"

_TEMPO_PATTERNS = [
    re.compile(rf"^set[_ ]tempo\s+{_NUM}$"),                                   # SET_TEMPO 142
    re.compile(rf"^(?:set |change |make )?(?:the )?(?:bpm|tempo)(?: to| at|=)?\s*{_NUM}(?: ?bpm)?$"),  # set bpm to 142
    re.compile(rf"^{_NUM} ?bpm$"),                                              # 142 bpm
]

_SIMPLE = {
    "play": "play", "start": "play", "start playback": "play",
    "stop": "stop", "stop playback": "stop",
    "record": "record", "rec": "record", "start recording": "record",
    "go_to_start": "go_to_start", "go to start": "go_to_start", "rewind": "go_to_start",
    "go to the start": "go_to_start", "back to start": "go_to_start",
    "undo": "undo", "undo that": "undo",
    "redo": "redo", "redo that": "redo",
    "save": "save", "save project": "save", "save the project": "save",
    "metronome": "toggle_metronome", "toggle metronome": "toggle_metronome",
    "toggle_metronome": "toggle_metronome", "toggle the metronome": "toggle_metronome",
    "open_mixer": "open_mixer", "open mixer": "open_mixer", "open the mixer": "open_mixer",
    "show mixer": "open_mixer", "show the mixer": "open_mixer", "mixer": "open_mixer",
    "status": "get_status", "get_status": "get_status", "ping": "get_status",
    "open channel rack": "open_channel_rack", "open the channel rack": "open_channel_rack",
    "show channel rack": "open_channel_rack", "show the channel rack": "open_channel_rack",
    "channel rack": "open_channel_rack", "open_channel_rack": "open_channel_rack",
    "open piano roll": "open_piano_roll", "open the piano roll": "open_piano_roll",
    "show piano roll": "open_piano_roll", "show the piano roll": "open_piano_roll",
    "piano roll": "open_piano_roll", "open_piano_roll": "open_piano_roll",
    "next pattern": "next_pattern", "next_pattern": "next_pattern",
    "previous pattern": "previous_pattern", "prev pattern": "previous_pattern", "previous_pattern": "previous_pattern",
    "duplicate pattern": "duplicate_pattern", "duplicate the pattern": "duplicate_pattern",
    "duplicate this pattern": "duplicate_pattern", "clone pattern": "duplicate_pattern",
    "clone the pattern": "duplicate_pattern", "duplicate_pattern": "duplicate_pattern",
    "quantize": "quantize", "quantise": "quantize", "quantize it": "quantize", "quantise it": "quantize",
}

# "metronome on", "turn the metronome off", "SET_METRONOME on", "click on"
_METRONOME = re.compile(
    r"^(?:set[_ ]metronome|(?:turn |switch )?(?:the )?(?:metronome|click)(?: to)?) (on|off|true|false)$"
    r"|^(?:turn|switch) (on|off) (?:the )?(?:metronome|click)$"
)


_TRACK = r"(?:track|insert|channel strip) (\d{1,3})|(master)"

# (pattern, command, parameter builder). Numbers are passed through as-is: the validator enforces ranges.
_PARAM_RULES = [
    (re.compile(rf"^(un)?mute (?:the )?(?:{_TRACK})$"), "mute_track",
     lambda m: {"track": _track(m, 2), "muted": not m.group(1)}),
    (re.compile(rf"^(un)?solo (?:the )?(?:{_TRACK})$"), "solo_track",
     lambda m: {"track": _track(m, 2), "solo": not m.group(1)}),
    (re.compile(rf"^(?:select )?(?:the )?(?:{_TRACK})$"), "select_track",
     lambda m: {"track": _track(m, 1)}),
    (re.compile(rf"^(?:turn |volume )?(up|down) (?:on )?(?:the )?(?:{_TRACK})$"), "volume_{0}",
     lambda m: {"track": _track(m, 2)}),
    (re.compile(rf"^(?:turn )?(?:the )?(?:{_TRACK}) (?:volume )?(up|down)$"), "volume_{2}",
     lambda m: {"track": _track(m, 1)}),
    (re.compile(r"^(?:go |skip |jump )?(back|forward|ahead) (\d{1,3}) bars?$"), "go_{0}_bars",
     lambda m: {"bars": int(m.group(2))}),
    (re.compile(r"^(?:select )?channel (\d{1,3})$"), "select_channel",
     lambda m: {"channel": int(m.group(1))}),
]


def _track(m, first_group):
    number, master = m.group(first_group), m.group(first_group + 1)
    return 0 if master else int(number)


def _command_name(template, m):
    word = {"ahead": "forward"}
    groups = [word.get(g, g) for g in m.groups()]
    return template.format(*groups)


class RuleInterpreter:
    """Exact-phrase and simple-pattern matching. Predictable, no network."""

    def interpret(self, text: str, context: Optional[dict] = None) -> Interpretation:
        return Interpretation(command=self._match(text), source="rules")

    def _match(self, text: str) -> Optional[dict]:
        stripped = text.strip()
        if stripped.startswith("{"):
            try:
                raw = json.loads(stripped)  # structured command typed directly
            except json.JSONDecodeError:
                return None
            return raw if isinstance(raw, dict) else None

        norm = re.sub(r"\s+", " ", stripped.lower()).rstrip(".!")
        if norm in _SIMPLE:
            return {"command": _SIMPLE[norm]}
        m = _METRONOME.match(norm)
        if m:
            return {"command": "set_metronome",
                    "parameters": {"enabled": (m.group(1) or m.group(2)) in ("on", "true")}}
        for pattern in _TEMPO_PATTERNS:
            m = pattern.match(norm)
            if m:
                return {"command": "set_tempo", "parameters": {"bpm": float(m.group(1))}}
        for pattern, command, build in _PARAM_RULES:
            m = pattern.match(norm)
            if m:
                return {"command": _command_name(command, m), "parameters": build(m)}
        return None
