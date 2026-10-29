# Architecture

## Layers

```
 VOICE ─► PHONE APP ─► BLUETOOTH ─► INTERPRETER (AI) ─► COMMAND ENGINE ─► FL CONTROLLER ─► FL LINK ─► FL STUDIO SCRIPT ─► FL STUDIO
 (P12)     (P10)        (P11)        (P9; v0.1: rules)   validate+verify    typed API        SysEx/MIDI    device_PirateBox.py
```

| Layer | Code | Knows about | Does NOT know about |
|---|---|---|---|
| Input source | `bridge/piratebox/inputs/` | its transport (console, HTTP, Bluetooth LE) | FL Studio, opcodes |
| Interpreter | `bridge/piratebox/interpreters.py` | text → raw command dict | validation, FL |
| Command registry | `bridge/piratebox/commands.py` + `protocol/commands.json` | the whitelist, parameter types and ranges | transports, FL |
| Command engine | `bridge/piratebox/engine.py` | command → controller call, state verification, results, journal | bytes, ports |
| FL controller | `bridge/piratebox/fl/controller.py` | one method per FL operation, opcodes | where commands came from |
| FL link | `bridge/piratebox/fl/link.py`, `ports.py`, `wire.py` | request/response matching, timeouts, reconnect, SysEx framing | what commands mean |
| FL script | `flstudio-controller/PirateBox/device_PirateBox.py` | FL Studio's official Python API | everything else |

### Interpretation (text → command)

`ChainInterpreter(RuleInterpreter, AIInterpreter)`. Exact phrases are matched locally. Only unmatched text goes to
the language model. The model is called with **structured outputs** whose schema is generated from
`commands.json`: an `anyOf` with one branch per command (with `const` names and typed, closed parameter
objects) plus an `unknown` branch with a clarification string. So the model cannot produce anything outside the
vocabulary, and new commands are picked up automatically. Ranges are left to the validator (single source of
truth). The engine passes the last known FL state as context, so relative requests like "10 BPM faster" work.
The provider sits behind `JSONModelProvider` (`ai/provider.py`); Claude Haiku 4.5 is the default because the
task is simple classification. It's the cheapest current Claude model, and latency matters for voice.

Every inbound path (typed text, JSON, later phone/AI) ends in `CommandEngine.handle_text()` or
`handle_command()`, so validation, verification and logging cannot be bypassed by a new input source.
The AI can only ever produce a *raw command dict*, which is validated like anything else.

## The key decision: how PirateBox talks to FL Studio

FL Studio's only officially supported automation API is **MIDI controller scripting**: a Python file
FL loads for a MIDI input device, with callbacks (`OnSysEx`, `OnMidiMsg`, `OnInit`, …) and modules
(`transport`, `general`, `mixer`, `ui`, `device`, …) that drive FL directly.

That script runs inside FL's embedded, sandboxed Python. **It can only be reached through MIDI.** The
bundled interpreter doesn't guarantee standard-library modules like `socket` or `json`, and a script
that blocks on a socket would freeze FL's UI thread.

So the options come down to:

| Option | Verdict |
|---|---|
| **Virtual MIDI port + SysEx to our own controller script** | **Chosen.** Officially supported, event-driven (no polling), bidirectional, tiny latency, no FL UI coupling. |
| Plain notes/CC to our script | Works, but 7-bit values and no request IDs, so tempo 142.5 and acknowledgements get awkward. SysEx carries arbitrary 7-bit payloads. |
| Sockets / WebSocket / named pipe / OSC directly into FL | FL has no such API. We would need a socket *inside* the script (unsupported, can block FL), or a plugin (heavy, and plugins can't drive transport or tempo properly). |
| Files polled by the script (`OnIdle`) | Supported-ish, but polling adds latency and disk writes, and it's more fragile than MIDI. |
| GUI automation (keystrokes/mouse) | Rejected: fragile, focus-dependent, can't read state back. |

**Downsides we accept:**
- Windows needs a virtual MIDI driver. We use **loopMIDI** (free). Windows 11's new *Windows MIDI
  Services* ships built-in loopback endpoints and may remove this dependency later.
- The user must select the controller script once in FL's MIDI settings. It's saved with FL's
  settings, and the appliance image will ship it preconfigured.
- Two ports are needed (one per direction). With a single loopback port, the bridge would receive
  its own requests back.
- The FL script can't import the bridge's code, so the wire constants are duplicated. A test
  (`tests/test_wire.py`) fails if the two copies ever differ.

Everything *outside* this link (phone ↔ bridge) is free to use WebSocket/Bluetooth/etc. The MIDI link
is an internal detail of the FL layer.

## Link wire format (v1)

All messages are SysEx with the non-commercial manufacturer ID `0x7D`. All data bytes are 7-bit.

```
F0 7D 4D 42 <ver> <type> <seq> <op> <payload…> F7
            'M' 'B'
```

| Field | Values |
|---|---|
| ver | `01` |
| type | `01` request (bridge→FL), `02` response (FL→bridge), `03` event (FL→bridge, unsolicited) |
| seq | 0–127, rolling; a response echoes its request's seq and op |
| op | `00` PING, `01` GET_STATE, `10` PLAY, `11` STOP, `12` RECORD, `13` GO_TO_START, `14` UNDO, `15` REDO, `16` SAVE, `17` SET_METRONOME, `18` SHOW_WINDOW, `20` SET_TEMPO, `21` MOVE_BARS, `30` CHANGE_PATTERN, `31` CLONE_PATTERN, `40` SET_TRACK_MUTE, `41` SET_TRACK_SOLO, `42` SELECT_TRACK, `43` NUDGE_TRACK_VOLUME, `50` QUANTIZE, `51` SELECT_CHANNEL |
| events | `01` HELLO (sent from `OnInit`; payload = FL API version u21 + state) |

Request payloads: SET_TEMPO = tempo in milli-BPM as **u21** (three 7-bit bytes, big-endian; 142 BPM →
142000 → `08 55 30`). SET_METRONOME = `00`/`01`. SHOW_WINDOW = window id (`0` mixer, `1` channel rack,
`2` playlist, `3` piano roll, `4` browser; anything else is rejected by the script).
MOVE_BARS = `<dir> <bars>` (dir `00` back / `01` forward, bars 1–64). CHANGE_PATTERN = `00` previous / `01` next.
SET_TRACK_MUTE / SET_TRACK_SOLO = `<track> <00|01>`. SELECT_TRACK = `<track>`. NUDGE_TRACK_VOLUME = `<track> <00 down|01 up>`
(tracks 0–125, 0 = master). SELECT_CHANNEL = 0-based channel index as u21. All others: empty.

Response payload: `<status> <tempo u21> <flags>`
- status: `00` OK, `01` unknown opcode, `02` bad args, `03` exec error, `04` version mismatch, `05` no effect (e.g. nothing to undo)
- flags: bit0 playing, bit1 recording, bit2 metronome, bit3 unsaved changes

Every response carries a state snapshot, so the bridge **verifies** outcomes instead of assuming them.
For example, after SET_TEMPO the reported tempo must equal the requested one. If it doesn't, the bridge
re-reads state once after 150 ms and otherwise reports `NOT_CONFIRMED`.

## FL API calls used

| Command | FL call |
|---|---|
| set_tempo | `general.processRECEvent(midi.REC_Tempo, bpm*1000, midi.REC_Control \| midi.REC_UpdateControl)` |
| play | `transport.start()` only if `not transport.isPlaying()` (start() toggles play/pause) |
| stop | `transport.stop()` |
| record | `transport.record()` if not `isRecording()`, then `start()` if not playing |
| go_to_start | `transport.setSongPos(0.0)` |
| undo / redo | `general.undoUp()` / `general.undoDown()`; `NO_EFFECT` if `general.getUndoHistoryLast()` didn't move |
| save | `transport.globalTransport(midi.FPT_Save, 1)`; bridge verifies `general.getChangedFlag() == 0` |
| set_metronome | `transport.globalTransport(midi.FPT_Metronome, 1)` only if `ui.isMetronomeEnabled()` differs (it toggles) |
| toggle_metronome | bridge reads state, then sends SET_METRONOME with the opposite value, which makes it verifiable |
| open_mixer / open_channel_rack / open_piano_roll | `ui.showWindow(wid)` + `ui.setFocused(wid)` (0 mixer, 1 channel rack, 3 piano roll) |
| next_pattern / previous_pattern | `patterns.jumpToPattern(patterns.patternNumber() ± 1)`; `NO_EFFECT` outside `1..patterns.patternCount()` |
| duplicate_pattern | `patterns.clonePattern(patterns.patternNumber())`; `NO_EFFECT` if `patternCount()` didn't grow |
| mute_track / solo_track | `mixer.muteTrack(t)` / `mixer.soloTrack(t)` only if `isTrackMuted` / `isTrackSolo` differs (they toggle); re-checked after |
| select_track | `mixer.setTrackNumber(t)` |
| volume_up / volume_down | `mixer.setTrackVolume(t, getTrackVolume(t) ± 0.05)`, clamped to 0..1 (0.8 = 0 dB); `NO_EFFECT` at the ends |
| go_back_bars / go_forward_bars | `transport.setSongPos(getSongPos() ± bars / getSongLength(midi.SONGLENGTH_BARS))`, clamped to 0..1 |
| quantize | `channels.quickQuantize(channels.selectedChannel())`; `NO_EFFECT` if nothing is selected |
| select_channel | `channels.selectOneChannel(n - 1)`; rejected if `n > channels.channelCount()` |
| state | `mixer.getCurrentTempo(True)`, `transport.isPlaying()`, `transport.isRecording()`, `ui.isMetronomeEnabled()`, `general.getChangedFlag()` |

## Security model

- Remote clients (HTTP now) must present a pairing token. They listen on localhost unless `--lan` is set,
  bodies are capped at 4 KB, and only one endpoint accepts input. The web page is static.

- The whitelist is enforced twice: by the bridge (`commands.json` + `ACTIONS`) and by the FL script
  (fixed opcode table plus range checks).
- There's no path from any input to a shell, the filesystem, keystrokes or `eval`. Structured JSON
  input is data only, and unknown fields are rejected.
- `requires_confirmation` exists in the schema for future destructive commands. The engine refuses
  such commands without `"confirmed": true`.
