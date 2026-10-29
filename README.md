# Music Computer: PirateBox v0.5

A dedicated, fanless, voice-controlled FL Studio appliance. This repo holds the control software.
**Built so far:** typed text, conversational text (Claude Haiku), or button press from the console, a browser, or the
**companion phone app** (paired by QR code, over Wi-Fi) → PirateBox → FL Studio.

```
TEXT / BUTTON ─► PIRATEBOX ─► virtual MIDI (SysEx) ─► PirateBox controller script inside FL Studio ─► FL Studio
     ▲                                                                                                    │
     └──────────────── JSON result, verified against FL's reported state ◄──────────────────────────────┘
```

## Folder structure

```
music-computer/
├── bridge/                    PirateBox application (Python)
│   └── piratebox/
│       ├── __main__.py        CLI entry point
│       ├── engine.py          CommandEngine: validate → execute → verify → result + journal
│       ├── commands.py        whitelist + strict validation (driven by protocol/commands.json)
│       ├── interpreters.py    text → command: exact rules first, then AI (chain)
│       ├── ai/                AI interpreter: provider interface, Claude provider, live phrase check
│       ├── journal.py         structured JSON-lines log of every stage
│       ├── pairing.py         persistent phone pairing token + QR code
│       ├── errors.py          status codes
│       ├── inputs/            input sources: console, HTTP + button page (web/), Bluetooth LE (ble.py)
│       ├── launcher.py        appliance startup: splash, loopMIDI, bridge, FL Studio, ready
│       └── fl/                FL Studio layer: wire format, MIDI link, controller, simulator
├── flstudio-controller/       FL-side script (runs inside FL Studio) + Windows installer
├── protocol/
│   ├── commands.json          command vocabulary, parameter ranges, status codes (shared with phone/AI)
│   └── MESSAGES.md            client ↔ bridge message envelope (phone app will speak this)
├── tests/                     pytest suite (runs without FL Studio)
├── docs/ARCHITECTURE.md       layers, FL communication decision, wire format, security
└── launcher/                  Windows autostart installer + splash logo (Phase 13)
```

## Architecture (short version)

- **Why MIDI?** FL Studio's only official automation API is its **Python MIDI controller scripting**, and the
  script can only be reached over MIDI. PirateBox sends compact **SysEx** requests over a virtual MIDI port.
  The script executes them with FL's own API (`transport.start()`, `general.processRECEvent(REC_Tempo…)`, …)
  and replies over a second port with a status code and a state snapshot. No mouse or keyboard automation.
- **Verification, not assumption.** A command only reports success once FL's reported state matches
  (for example, the tempo really is 142).
- **Swappable inputs.** The console, and later the phone, Bluetooth or an AI interpreter, all call the same
  `CommandEngine`. Only `bridge/piratebox/fl/` knows FL Studio exists.

Full rationale and trade-offs: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Installation (Windows PC with FL Studio)

Requirements: Windows 10/11, FL Studio 21 or later recommended (MIDI scripting API),
Python 3.10+.

1. **Install loopMIDI** (free, Tobias Erichsen): https://www.tobias-erichsen.de/software/loopmidi.html
   Create **two** ports with exactly these names:
   - `PirateBox ToFL`
   - `PirateBox FromFL`

   Tick "Autostart loopMIDI" in its tray menu so the ports exist after reboot.
2. **Install the bridge:**
   ```powershell
   cd music-computer
   py -m venv .venv
   .venv\Scripts\pip install -r bridge\requirements.txt
   ```
3. **Install the FL controller script:**
   ```powershell
   powershell -ExecutionPolicy Bypass -File flstudio-controller\install.ps1
   ```
   This copies `device_PirateBox.py` to `Documents\Image-Line\FL Studio\Settings\Hardware\PirateBox\`.

## FL Studio setup (one time)

1. Start FL Studio → **Options → MIDI settings**.
2. **Input** list: select `PirateBox ToFL`, click **Enable**, set **Controller type** to
   **PirateBox Controller**, and set **Port** to `42` (any free number works).
3. **Output** list: select `PirateBox FromFL` and set its **Port** to the **same number (`42`)**.
   This links the script's replies to that output.
4. Leave `PirateBox FromFL` *disabled* in the Input list and `PirateBox ToFL` unassigned in the Output list.
5. Optional check: **View → Script output** should show `[PirateBox] controller script loaded (link protocol v1)`.

## Running PirateBox

```powershell
cd music-computer\bridge
..\.venv\Scripts\python -m piratebox              # interactive console
..\.venv\Scripts\python -m piratebox -v           # also print every pipeline stage
..\.venv\Scripts\python -m piratebox --once "SET_TEMPO 142"   # one command, exit code 0 = success
..\.venv\Scripts\python -m piratebox --list-ports # show MIDI port names
..\.venv\Scripts\python -m piratebox --simulate   # no FL Studio needed (built-in simulator)
..\.venv\Scripts\python -m piratebox --http       # + button test client in the browser
..\.venv\Scripts\python -m piratebox --http --lan # + reachable from a phone on the same Wi-Fi
..\.venv\Scripts\python -m piratebox --http --lan --ble   # + phone over Bluetooth (pip install bless)
..\.venv\Scripts\python -m piratebox.launcher  # appliance mode: splash, loopMIDI, FL, ready (see launcher/)
```

### Voice and Bluetooth (Phases 11–12 / v0.5)

- **Voice:** in the store/TestFlight build, hold **Hold to talk** on the phone and speak ("make it 142", "mute
  track 3", "back four bars"). The phone turns speech into text and sends it like typed text, so it uses the same
  rules and, with `--ai`, Claude. Nothing new is needed on the computer.
- **Bluetooth:** start with `--ble` (needs `bless`, and a Bluetooth adapter that can act as a peripheral; most
  Intel Wi-Fi/Bluetooth cards can). The pairing QR then includes the Bluetooth name, and the app shows a
  **Wi-Fi / Bluetooth** switch. Bluetooth works with no network at all, within about 10 m.
- **Startup:** `launcher\install-autostart.ps1 [-AI] [-BLE]` makes the PC boot straight into PirateBox. See [launcher/README.md](launcher/README.md).

### Phone app (Phase 10 / v0.4)

Run `python -m piratebox --http --lan`. PirateBox prints a **pairing QR code** in the terminal. It also
serves the code at `http://127.0.0.1:7142/pair`, which only this computer can open. Scan it with the PirateBox
Remote app (available on the App Store and Google Play).

- The pairing token is created once and saved: `%LOCALAPPDATA%\PirateBox\pairing.json` on Windows,
  `~/.piratebox/pairing.json` elsewhere. Phones stay paired across restarts. `--new-token` un-pairs every phone.
- The app shows *Computer connected · FL Studio ready*, the tempo, and PLAY/REC/CLICK/UNSAVED lights, polling every 2 s.
  It has transport, BPM, bar-jump, undo/redo/save/click, pattern and window buttons, hold-to-talk voice and a text box. With `--ai`, the text box accepts conversational text.

### AI interpreter (Phase 9 / v0.3)

With `--ai`, conversational text like "let's make this one 140", "take it back to the top" or "kill the click" is
mapped to a command by **Claude Haiku 4.5** (`claude-haiku-4-5`).

1. Get an API key at https://console.anthropic.com and set it in the environment:
   `setx ANTHROPIC_API_KEY sk-ant-...` on Windows (then open a new terminal), or `export ANTHROPIC_API_KEY=...` on macOS.
2. Run `python -m piratebox --ai` (combine with `--http`, `--simulate`, etc.).
3. Optional live check: `python -m piratebox.ai.check` runs 26 phrases from the spec through the real API and
   prints what each one maps to. That's about 26 short requests, a few cents at most. It never touches FL Studio.

How it works and why it's safe:
- **Exact commands never reach the AI.** `SET_TEMPO 142`, `PLAY`, `metronome on`, the buttons and JSON are all handled
  locally first. They're instant, free, and work offline.
- **Only whitelisted commands.** The AI must answer using a JSON schema generated from `protocol/commands.json`
  (structured outputs), so it can only choose a whitelisted command or `unknown`. Its answer then goes through
  the same validation as everything else, so "set it to 900" still gets `OUT_OF_RANGE`.
- **Unclear requests** return `NEEDS_CLARIFICATION` with a short message for the phone, e.g. "I can't edit
  hi-hat patterns yet…". So do out-of-scope ones ("open the piano roll").
- **Relative changes** like "bump it up 10 BPM" use the last tempo PirateBox saw.
- **Failures are visible.** No key, no internet or a service error gives `AI_UNAVAILABLE` with the reason. Exact
  commands keep working.
- **Logging:** each AI call is journaled with model, latency and token counts.
- **Swappable provider:** implement `JSONModelProvider` in `ai/provider.py`.

### Button test client (Phase 8 / v0.2)

With `--http`, PirateBox prints a URL such as `http://127.0.0.1:7142/#token=Xy3…`. Open it in a browser
to get transport, BPM 140/142/150, undo/redo/save/click/mixer buttons and a text box. A live display shows
tempo, PLAY/REC/CLICK/UNSAVED lights, and whether FL Studio is connected.

- **From a phone:** run with `--lan` and open the second printed URL (`http://<pc-lan-ip>:7142/#token=…`) on a phone
  on the same Wi-Fi. Windows Firewall will ask to allow Python on first run; allow **Private networks** only.
- **Token:** every request must carry the pairing token. It's random for each run unless you set `--token` or `PIRATEBOX_TOKEN`.
- **Protocol:** the page uses the same JSON messages the phone app will use ([protocol/MESSAGES.md](protocol/MESSAGES.md)).

On startup it pings FL and prints `FL Studio READY (tempo 140 BPM)` or the reason it isn't ready.

## How to test

**Automated (any OS, no FL Studio needed):**
```bash
.venv/bin/pip install -r bridge/requirements-dev.txt   # Windows: .venv\Scripts\...
.venv/bin/pytest tests
```
The suite runs the **real FL-side script** against a simulated FL API. On macOS/Linux it also runs the whole
pipeline over real OS virtual MIDI ports.

**v0.1 acceptance test (real FL Studio).** Open any project, run the console, and type:

| Type | Expect in FL Studio | Expect in console |
|---|---|---|
| `SET_TEMPO 142` | Tempo display shows 142.000 | `"success": true, "status": "OK", "message": "Tempo set to 142 BPM"` |
| `set BPM to 120` | 120.000 | same, 120 |
| `PLAY` | playback starts | `"playing": true` |
| `STOP` | playback stops | `"playing": false` |
| `GO_TO_START` (while playing) | playhead jumps to the start | `Moved to start` |
| `RECORD` | record armed and playback starts | `"recording": true, "playing": true` |
| `UNDO` (after changing something, e.g. tempo) | the change is undone | `Undone` |
| `REDO` | the change is re-applied | `Redone` |
| `UNDO` repeatedly on a fresh project | nothing | `NO_EFFECT` "Nothing to undo" |
| `SAVE` on a saved-before project | title bar loses its unsaved marker | `Project saved`, `"unsaved_changes": false` |
| `SAVE` on an untitled project | Save As dialog appears | `NOT_CONFIRMED` explaining the dialog |
| `TOGGLE_METRONOME`, `metronome on` | metronome button toggles, or turns on | `Metronome on/off` |
| `OPEN_MIXER` | mixer window shows and gets focus | `Mixer open` |
| `SET_TEMPO 999` | nothing | `OUT_OF_RANGE`, nothing sent to FL |
| close FL, then `PLAY` | — | `FL_TIMEOUT` with an explanation |

**v0.5 acceptance test (real FL Studio)**, with a project that has a few patterns, mixer inserts and channels:

| Type | Expect in FL Studio | Expect in console |
|---|---|---|
| `open the channel rack`, `piano roll` | window shows and gets focus | `Channel rack open`, `Piano roll open` |
| `next pattern`, `previous pattern` | pattern selector moves by one | `Next pattern`; `NO_EFFECT` at the first/last |
| `duplicate pattern` | a copy of the current pattern appears | `Pattern duplicated` |
| `mute track 1` twice, then `unmute track 1` | insert 1 muted, stays muted, then unmuted | `Track 1 muted` / `unmuted` |
| `solo track 2`, `unsolo track 2` | insert 2 soloed, then not | `Track 2 soloed` / `unsoloed` |
| `select track 3` | insert 3 selected in the mixer | `Track 3 selected` |
| `turn up track 1` ×3, `track 1 down` | insert 1 fader rises in small steps, then falls | `Track 1 up` / `down` |
| `forward 4 bars`, `back 1 bar` (song mode) | playhead moves 4 bars, then back 1 | `Forward 4 bars`, `Back 1 bar` |
| select a channel with notes, `quantize` | its notes snap to the grid | `Quantized the selected channel` |
| `channel 2`, `channel 99` | 2nd channel selected; nothing | `Channel 2 selected`; `FL_REJECTED` "There is no channel 99…" |

> **Check on real FL:** `duplicate pattern` uses `patterns.clonePattern`, which needs a recent FL Studio (2024+).
> On older versions it reports `FL_EXEC_ERROR`. Bar moves use song position, so in pattern mode they move
> within the pattern.

Repeat each several times. Then run `--http` and repeat the button presses from the browser and from a phone.

> **Undo caveat to check on real FL:** undo/redo detect "nothing to undo" by checking that FL's undo position
> moved. If real FL applies undo asynchronously, UNDO would wrongly report `NO_EFFECT` while still working.
> If you see that, tell me and I'll switch to re-checking after a short delay.

## Supported commands

| Command | Text forms | JSON |
|---|---|---|
| set_tempo | `SET_TEMPO 142`, `set bpm to 142`, `tempo 142`, `142 bpm` | `{"command":"set_tempo","parameters":{"bpm":142}}` (20–300, up to 3 decimals) |
| play | `PLAY`, `start` | `{"command":"play"}` |
| stop | `STOP` | `{"command":"stop"}` |
| record | `RECORD`, `start recording` | `{"command":"record"}` |
| go_to_start | `GO_TO_START`, `go to start`, `rewind` | `{"command":"go_to_start"}` |
| undo | `UNDO`, `undo that` | `{"command":"undo"}` |
| redo | `REDO`, `redo that` | `{"command":"redo"}` |
| save | `SAVE`, `save the project` | `{"command":"save"}` (verified via FL's unsaved-changes flag) |
| toggle_metronome | `TOGGLE_METRONOME`, `metronome` | `{"command":"toggle_metronome"}` |
| set_metronome | `metronome on`, `turn the metronome off`, `SET_METRONOME on` | `{"command":"set_metronome","parameters":{"enabled":true}}` |
| open_mixer | `OPEN_MIXER`, `open the mixer` | `{"command":"open_mixer"}` |
| open_channel_rack | `open the channel rack`, `channel rack` | `{"command":"open_channel_rack"}` |
| open_piano_roll | `open the piano roll`, `piano roll` | `{"command":"open_piano_roll"}` |
| next_pattern / previous_pattern | `next pattern`, `previous pattern` | `{"command":"next_pattern"}` |
| duplicate_pattern | `duplicate the pattern`, `clone pattern` | `{"command":"duplicate_pattern"}` |
| mute_track | `mute track 3`, `unmute master` | `{"command":"mute_track","parameters":{"track":3,"muted":true}}` (0 = master, 1–125) |
| solo_track | `solo track 3`, `unsolo track 3` | `{"command":"solo_track","parameters":{"track":3,"solo":true}}` |
| select_track | `select track 3`, `insert 3` | `{"command":"select_track","parameters":{"track":3}}` |
| volume_up / volume_down | `turn up track 3`, `track 3 down` | `{"command":"volume_up","parameters":{"track":3}}` (±0.05 fader, ~1.5 dB) |
| go_back_bars / go_forward_bars | `back 4 bars`, `skip forward 1 bar` | `{"command":"go_back_bars","parameters":{"bars":4}}` (1–64) |
| quantize | `quantize`, `quantise` | `{"command":"quantize"}` (selected channel, current pattern) |
| select_channel | `channel 2`, `select channel 2` | `{"command":"select_channel","parameters":{"channel":2}}` (1 = top of the rack) |
| get_status | `STATUS`, `ping` | `{"command":"get_status"}` |

Anything else is rejected. Results look like:

```json
{"request_id": "c8953b3f", "success": true, "status": "OK", "message": "Tempo set to 142 BPM",
 "state": {"tempo": 142.0, "playing": false, "recording": false, "metronome": false},
 "command": "set_tempo", "parameters": {"bpm": 142}}
```

Status codes are listed in [protocol/commands.json](protocol/commands.json).

## Logs

Every request is written to `logs/piratebox-YYYY-MM-DD.jsonl` as one line per stage, sharing a `request_id`:
`input → interpreted → validated → sent → acknowledged → (reverified) → result`.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `FL_NOT_CONNECTED … port 'PirateBox ToFL' not found` | loopMIDI isn't running or the port names differ. Run `--list-ports`, or pass `--to-fl` / `--from-fl`. |
| `FL_TIMEOUT` | FL isn't running, the input isn't **enabled**, the controller type isn't *PirateBox Controller*, or the **output port number doesn't match the input's**. Check View → Script output. |
| Script not in the Controller type list | The file isn't at `Documents\Image-Line\FL Studio\Settings\Hardware\PirateBox\device_PirateBox.py` (check OneDrive-redirected Documents). Restart FL. |
| `FL_VERSION_MISMATCH` / `FL_REJECTED` "script out of date" | Re-run `install.ps1` and restart FL. |
| `NOT_CONFIRMED` | FL acknowledged but the state didn't change (e.g. tempo automation in the project overriding it). Check the `state` in the result. |
| RECORD opens a "what to record" dialog | That's an FL recording option. Set the recording filter in FL's record menu so no prompt appears. |
| Ports vanish after reboot | Enable loopMIDI's autostart. |
| `Could not start HTTP test client on port 7142` | Another app uses that port. Pass `--http-port 7200`. |
| Phone can't open the page | Use `--lan`, make sure phone and PC are on the same Wi-Fi, and allow Python through Windows Firewall (Private). Some guest/mesh networks isolate devices. |
| `AI_UNAVAILABLE` "no Anthropic credentials" | Set `ANTHROPIC_API_KEY` and open a new terminal. Exact commands work regardless. |
| `AI_UNAVAILABLE` "timed out" / "cannot reach" | No internet, or the API is slow. The timeout is 8 s with 1 retry. |
| AI maps a phrase wrongly | Add the phrase to `ai/check.py`'s CASES, run the check, and send me the output. Common phrases can also go in the rule table in `interpreters.py` (free and instant). |
| Page says "Not paired" | The URL is missing `#token=…`. Use the exact URL PirateBox printed, or paste the token. |

## Roadmap

| Phase | | Status |
|---|---|---|
| 1 | Project structure + architecture | ✅ |
| 2 | FL communication method (MIDI scripting over SysEx) | ✅ |
| 3–5 | FL controller, PirateBox, SET_TEMPO | ✅ built + simulated; **awaiting real-FL acceptance test** |
| 6 | PLAY/STOP/RECORD/GO_TO_START/UNDO/REDO/SAVE/METRONOME/OPEN_MIXER | ✅ built + simulated; awaiting real FL |
| 7 | Structured command protocol (`commands.json`, `MESSAGES.md`) | ✅ v1 |
| 8 | Test client (browser buttons, phone over Wi-Fi = v0.2 target) | ✅ built, verified in browser against simulator |
| 9 | AI interpreter (Claude Haiku 4.5, structured outputs, rules-first chain) | ✅ built + unit-tested; live check needs an API key |
| 10 | Phone app over Wi-Fi (QR pairing, status, buttons, text) | ✅ built; verified in the Expo web preview against the simulator; needs a run on a real phone |
| 11 | Bluetooth LE link (`--ble`; phone switches Wi-Fi/Bluetooth) | ✅ built; framing + auth tested, advertising verified on a Mac; needs a real phone ↔ PC test |
| 12 | Voice: hold-to-talk on the phone → speech recognition → same interpreter as text | ✅ built; needs a real phone (not in Expo Go) |
| 13 | Branded launcher / kiosk startup (`launcher/`) | ✅ built + tested with a fake machine and the simulator; needs a Windows run |
| — | v0.5 commands: windows, patterns, mixer tracks, bars, quantize, channels | ✅ built + simulated; awaiting real FL |
| 14 | N100/N150 hardware selection + Windows appliance setup | 📄 see `docs/HARDWARE.md` |
| 15 | Thermal and stability testing (4 test suite) | 📄 see `docs/HARDWARE.md` |
| 16 | Enclosure (off-the-shelf / 3D-print / aluminium) + front-panel LED hook | 📄 see `docs/HARDWARE.md` |
