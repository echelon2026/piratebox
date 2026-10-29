"""Interactive terminal input (v0.1)."""

import json

from .base import InputSource

HELP = """\
Commands (case-insensitive):
  SET_TEMPO 142   |  set bpm to 142  |  tempo 142  |  142 bpm
  PLAY            |  STOP            |  RECORD     |  GO_TO_START
  UNDO | REDO | SAVE | TOGGLE_METRONOME | metronome on/off | OPEN_MIXER
  STATUS          check FL Studio is connected
  With --ai, anything conversational works too: "make it 140", "let's hear it", "kill the click"
  {"command": "set_tempo", "parameters": {"bpm": 142}}   structured JSON
  help, quit
"""


class ConsoleInput(InputSource):
    name = "console"

    def run(self) -> None:
        print(HELP)
        while True:
            try:
                line = input("piratebox> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if not line:
                continue
            if line.lower() in ("quit", "exit", "q"):
                return
            if line.lower() in ("help", "?"):
                print(HELP)
                continue
            result = self.engine.handle_text(line, source=self.name)
            print(json.dumps(result, indent=2))
