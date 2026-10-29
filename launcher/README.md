# launcher (Phase 13)

Turns the PC into a PirateBox appliance. At login it shows a fullscreen PirateBox logo and works through:

1. **MIDI link**: checks that the `PirateBox ToFL` / `PirateBox FromFL` loopMIDI ports exist, and starts loopMIDI if they don't.
2. **PirateBox**: starts the bridge in the background (`--http --lan`, no console window).
3. **FL Studio**: launches the newest `FL64.exe` under `C:\Program Files\Image-Line\`, unless FL is already open.
4. **Ready**: waits (up to 3 minutes) until FL Studio and the controller script answer, then fades out.

The phone app polls the bridge, so it switches to "FL Studio ready" as soon as step 4 passes. After the
splash fades, the launcher keeps running quietly and restarts the bridge if it ever stops.
If a step fails, the splash shows what's wrong and how to fix it. Press Esc to close it.

Code: `bridge/piratebox/launcher.py` (tests in `tests/test_launcher.py`).

## Install (Windows)

```powershell
powershell -ExecutionPolicy Bypass -File launcher\install-autostart.ps1        # rules only
powershell -ExecutionPolicy Bypass -File launcher\install-autostart.ps1 -AI    # also the Claude interpreter
powershell -ExecutionPolicy Bypass -File launcher\install-autostart.ps1 -Remove
```

This puts a `PirateBox` shortcut in your Startup folder. It needs the Python installer's "tcl/tk" option,
which is on by default.

For a true appliance, also:
- turn on automatic login (`netplwiz`, then untick "Users must enter a user name and password"),
- in loopMIDI, keep "Autostart loopMIDI" ticked so the ports exist after a reboot,
- in FL Studio, enable the PirateBox controller once (Options > MIDI settings). FL remembers it.

## Try it without Windows

```bash
cd bridge
python -m piratebox.launcher --headless --simulate      # simulated FL; prints the steps
```

Options: `--headless` (print instead of splash), `--simulate`, `--ai`, `--fl-path "D:\FL\FL64.exe"`, `--port 7142`.
