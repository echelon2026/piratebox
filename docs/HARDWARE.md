# PirateBox Hardware Guide (Phases 14–16)

Phases 14–16 cover turning a mini PC into a finished PirateBox appliance: hardware selection, thermal validation, and enclosure.

---

## Phase 14 – Hardware selection and Windows setup

### Recommended hardware

| Component | Minimum | Recommended |
|---|---|---|
| CPU | Intel N100 | Intel N150 or N305 |
| RAM | 8 GB LPDDR5 | 16 GB LPDDR5 |
| Storage | 256 GB NVMe | 512 GB NVMe |
| USB ports | 2× USB-A + 1× USB-C | 4× USB-A + 2× USB-C |
| Video out | 1× HDMI | HDMI + USB-C DP |
| Form factor | Any mini PC | Passively cooled / fanless |
| TDP | ≤10 W | ≤6 W (N100 passively cooled) |

**Why N100 / N150:** single-core boost up to 3.4 GHz handles FL Studio's real-time audio well; the 6 W TDP allows passive (silent) cooling, which is important in a music environment. Boards and complete mini PCs are available from Beelink, GMKtec, MINISFORUM, and similar.

**Audio interface:** the bridge talks to FL over virtual MIDI (loopMIDI), so any standard USB audio interface works. For the appliance, plug an audio interface into a USB-A port and connect monitors/headphones to it.

---

### Windows 11 installation

1. **Install Windows 11 Pro** — the Home edition can be used, but Pro makes it easier to disable telemetry and manage startup services via Group Policy.
2. **Create a single local account** named `piratebox` (no Microsoft account required).
3. **Enable autologin:**
   ```
   Win+R → netplwiz → untick "Users must enter a user name and password" → set password
   ```
4. **Disable Fast Startup** (can interfere with loopMIDI port persistence):
   ```
   Control Panel → Power Options → Choose what power buttons do → uncheck "Turn on fast startup"
   ```
5. **Set power plan to High Performance:**
   ```powershell
   powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c
   ```
6. **Disable Windows Update during sessions** — set Active Hours to cover your whole day in Settings → Windows Update → Advanced Options.

---

### Software stack installation order

```
1. loopMIDI              https://www.tobias-erichsen.de/software/loopmidi.html
   ↳ Create ports "PirateBox ToFL" and "PirateBox FromFL"
   ↳ Tick "Autostart loopMIDI" in the tray icon

2. Python 3.12 (64-bit)  https://www.python.org/downloads/windows/
   ↳ During install: tick "Add to PATH" and "tcl/tk" (for the splash launcher)

3. FL Studio (current)   https://www.image-line.com
   ↳ Register and activate before running for the first time
   ↳ Options > MIDI settings > add "PirateBox ToFL" as input, "PirateBox FromFL" as output
   ↳ Select device_PirateBox.py as the controller script (see step 5)

4. PirateBox bridge      from this repo's bridge/ folder
   pip install -e bridge/
   (or copy the bridge/ folder, cd into it, pip install -e .)

5. FL controller script  copy flstudio-controller/PirateBox/ into:
   %USERPROFILE%\Documents\Image-Line\FL Studio\Settings\Hardware\PirateBox\

6. Autostart launcher
   powershell -ExecutionPolicy Bypass -File launcher\install-autostart.ps1
   (add -AI to enable the Claude interpreter; needs ANTHROPIC_API_KEY in the environment)
```

**Verify with the simulator first:**
```powershell
python -m piratebox --once "SET_TEMPO 120"
```
Should return `{"success": true, "status": "OK", ...}`. If FL Studio is running, this drives the real app.

---

### Windows tuning checklist (appliance mode)

- [ ] Autologin enabled (`netplwiz`)
- [ ] `PirateBox` shortcut in `shell:startup`
- [ ] loopMIDI autostart ticked in tray
- [ ] FL Studio controller script selected in MIDI settings (saved in FL's config file, survives reboots)
- [ ] Audio interface driver installed, default output set to the interface
- [ ] ASIO driver installed (ASIO4ALL or interface-native); set buffer to 256–512 samples
- [ ] High-performance power plan active
- [ ] Fast Startup disabled
- [ ] Windows Defender real-time scan exclusion for FL's project folder (reduces disk I/O spikes)
- [ ] No screen saver, display timeout set to "Never" (appliance keeps the screen on for the splash)

---

## Phase 15 – Thermal and stability testing

Run these tests before closing up the enclosure. All temperatures recorded with [HWiNFO64](https://www.hwinfo.com) or Windows' built-in `Get-Counter`.

### Test 1 — Idle soak (30 min)

Boot, log in, let PirateBox launcher start FL Studio, then leave untouched for 30 minutes.

| Metric | Pass threshold |
|---|---|
| CPU package temp | < 70 °C |
| Bridge process CPU | < 5% |
| FL Studio CPU meter | < 10% |
| No bridge restart | ✓ |

### Test 2 — Sustained MIDI load (15 min)

Run the acceptance test loop repeatedly:
```powershell
for ($i = 0; $i -lt 900; $i++) { python -m piratebox --once "PLAY"; Start-Sleep -Seconds 1 }
```

| Metric | Pass threshold |
|---|---|
| CPU package temp | < 85 °C |
| No FL Studio freeze | ✓ |
| Bridge responds to all commands | ✓ |

### Test 3 — BLE + Wi-Fi simultaneously (5 min)

Pair the phone app over BLE, also connect over Wi-Fi (`--lan`), and interleave commands from both.

| Metric | Pass threshold |
|---|---|
| No dropped commands | ✓ |
| Round-trip latency (BLE) | < 200 ms avg |
| Round-trip latency (Wi-Fi) | < 100 ms avg |

### Test 4 — Cold start timing

Power off, then time from pressing the power button to "FL Studio READY" on the phone app.

| Target | < 90 seconds |

### Thermal throttling check

If any test fails the temperature threshold, check:
1. Is the heat sink/paste properly seated?
2. Is the mini PC's vent unobstructed?
3. Try reducing Windows power plan TDP limit via `powercfg`.
4. Consider adding a small 40 mm fan inside the enclosure (see Phase 16).

---

## Phase 16 – Enclosure

### Option A — Off-the-shelf mini PC case (fastest)

Most N100 boards come with a case. If the mini PC vendor provides a case, this is ready to go after the software setup. Pros: pre-engineered thermals, standard. Cons: generic look.

### Option B — 3D-printed custom enclosure

Print a box around an N100 **bare board** (e.g. Topton NM-N100 board). Key dimensions:
- Board: ~100 × 72 mm (verify for your board)
- Clearance above board: ≥ 25 mm for heat sink
- Side vents: ≥ 15% of side area, offset so airflow crosses the SOC
- Front panel: USB-C (power) + 2× USB-A (audio interface + keyboard) + LED ring or strip

Recommended filament: **PETG or ASA** (higher heat tolerance than PLA; ambient near the board can reach 45 °C).

The PirateBox logo is available at `mobile/assets/logo-source.png`; scale and emboss it on the lid.

### Option C — Aluminium extrusion chassis

An aluminium profile chassis (e.g. Hammond 1455 series, or a custom Protocase order) doubles as a heat sink if the N100's heat pipe contacts the lid. This is the premium option for live use.

### Cable management inside the enclosure

| Cable | Route |
|---|---|
| USB-C power | short 10 cm USB-C extension to back panel |
| Audio interface | USB-A to back panel |
| HDMI | optional; leave a pass-through port or omit entirely for headless |
| Antenna (Wi-Fi / BT) | internal; route away from power cables |

### Front-panel LED

The launcher (`bridge/piratebox/launcher.py`) exposes a simple hook for an external LED: it calls `on_state_change(state: str)` where `state` is one of `STARTING`, `WAITING_FOR_FL`, `READY`, `ERROR`. Wire a USB-connected addressable LED strip to a separate microcontroller (e.g. Pi Pico) and listen on a serial port. The launcher already prints machine-readable JSON to stdout; the Pico can parse it.

---

## Phase 14–16 sign-off checklist

- [ ] Phase 14: all software installed, `--once SET_TEMPO 120` succeeds with FL running
- [ ] Phase 14: autologin + launcher autostart → phone shows "FL Studio ready" after cold boot
- [ ] Phase 15: all four thermal/stability tests pass
- [ ] Phase 16: enclosure assembled, all ports accessible, no rattles
- [ ] Phase 16: acoustic test — fan noise ≤ 35 dB at 1 m in a quiet room (or fanless)
- [ ] Final: hand the unit to a non-technical user and have them pair + send a voice command without assistance
