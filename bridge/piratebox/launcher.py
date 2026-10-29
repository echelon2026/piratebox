"""
StartupLauncher (Phase 13): turns the PC into a PirateBox appliance at login.

    python -m piratebox.launcher                 fullscreen logo, start everything, fade out when ready
    python -m piratebox.launcher --headless      same steps, printed to the console (no window)
    python -m piratebox.launcher --simulate      simulated FL Studio: skips loopMIDI and FL (for testing)

Steps, in order (each one shown on the splash screen):
    1. MIDI link   the loopMIDI ports exist (starts loopMIDI if they don't and it is installed)
    2. PirateBox   start the bridge (HTTP + LAN, same as `python -m piratebox --http --lan --no-console`)
    3. FL Studio   launch FL Studio unless it's already running
    4. Ready       wait until FL Studio and the controller script answer a status request

The phone app polls the bridge every 2 s, so it shows "FL Studio ready" as soon as step 4 passes.
After that the launcher stays running in the background and restarts the bridge if it ever exits.
"""

import argparse
import glob
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from .pairing import load_or_create_token

BRIDGE_DIR = Path(__file__).resolve().parents[1]
LOGO = Path(__file__).resolve().parents[2] / "launcher" / "logo.png"

FL_GLOBS = [
    r"C:\Program Files\Image-Line\FL Studio*\FL64.exe",
    r"C:\Program Files\Image-Line\FL Studio*\FL.exe",
    r"C:\Program Files (x86)\Image-Line\FL Studio*\FL.exe",
]
LOOPMIDI_EXE = r"C:\Program Files (x86)\Tobias Erichsen\loopMIDI\loopMIDI.exe"


@dataclass
class Settings:
    to_fl: str = "PirateBox ToFL"
    from_fl: str = "PirateBox FromFL"
    port: int = 7142
    simulate: bool = False
    ai: bool = False
    ble: bool = False
    fl_path: Optional[str] = None
    ports_timeout: float = 15.0
    bridge_timeout: float = 20.0
    fl_timeout: float = 180.0  # FL Studio with a big project and plugin scan can take a while
    poll: float = 1.0


class StepFailed(Exception):
    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.message = message
        self.hint = hint


# ---- the outside world, wrapped so tests can replace it ---------------------------------------

class System:
    """Everything the launcher does to the machine. Tests pass a fake."""

    def midi_ports(self) -> List[str]:
        from .fl.ports import list_ports
        found = list_ports()
        return list(found.get("inputs", [])) + list(found.get("outputs", []))

    def process_running(self, exe_name: str) -> bool:
        if sys.platform != "win32":
            return False
        try:
            out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe_name}", "/NH"],
                                 capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return False
        return exe_name.lower() in out.lower()

    def find_file(self, patterns: List[str]) -> Optional[str]:
        matches = sorted({m for p in patterns for m in glob.glob(p)}, reverse=True)  # newest FL version first
        return matches[0] if matches else None

    def exists(self, path: str) -> bool:
        return Path(path).exists()

    def start(self, argv: List[str], cwd: Optional[Path] = None) -> subprocess.Popen:
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NO_WINDOW if Path(argv[0]).name.lower().startswith("python") else 0
        return subprocess.Popen(argv, cwd=str(cwd) if cwd else None, creationflags=flags)

    def status(self, port: int, token: str) -> Optional[dict]:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/message", method="POST",
            data=json.dumps({"type": "status"}).encode(),
            headers={"Content-Type": "application/json", "X-PirateBox-Token": token})
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                return json.loads(r.read())
        except (OSError, ValueError, urllib.error.URLError):
            return None

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def now(self) -> float:
        return time.monotonic()


# ---- the steps -----------------------------------------------------------------------------------

Report = Callable[[str, str, str], None]  # (step, state: "run"|"ok"|"fail"|"skip", detail)


class Launcher:
    STEPS = ("MIDI link", "PirateBox", "FL Studio", "Ready")

    def __init__(self, settings: Settings, report: Report, system: System = None,
                 python: str = sys.executable, token: str = None):
        self.s = settings
        self.report = report
        self.sys = system or System()
        self.python = python
        self.token = token or load_or_create_token()
        self.bridge: Optional[subprocess.Popen] = None

    def run(self) -> bool:
        steps = [("MIDI link", self.ensure_ports), ("PirateBox", self.start_bridge),
                 ("FL Studio", self.start_fl), ("Ready", self.wait_ready)]
        for name, step in steps:
            self.report(name, "run", "")
            try:
                detail = step()
            except StepFailed as e:
                self.report(name, "fail", e.message + (f"\n{e.hint}" if e.hint else ""))
                return False
            self.report(name, "skip" if detail == "skipped" else "ok", "" if detail == "skipped" else (detail or ""))
        return True

    def _wait(self, timeout: float, check: Callable[[], bool]) -> bool:
        deadline = self.sys.now() + timeout
        while True:
            if check():
                return True
            if self.sys.now() >= deadline:
                return False
            self.sys.sleep(self.s.poll)

    def _ports_present(self) -> bool:
        names = self.sys.midi_ports()
        # Windows/loopMIDI can append an index ("PirateBox ToFL 1"), so match by prefix.
        return all(any(n.startswith(want) for n in names) for want in (self.s.to_fl, self.s.from_fl))

    def ensure_ports(self) -> str:
        if self.s.simulate:
            return "skipped"
        if self._ports_present():
            return "ports found"
        if not self.sys.process_running("loopMIDI.exe") and self.sys.exists(LOOPMIDI_EXE):
            self.sys.start([LOOPMIDI_EXE])
            if self._wait(self.s.ports_timeout, self._ports_present):
                return "started loopMIDI"
        raise StepFailed(f"MIDI ports '{self.s.to_fl}' and '{self.s.from_fl}' not found.",
                         "Open loopMIDI and create both ports (see the README), then restart.")

    def start_bridge(self) -> str:
        if self.sys.status(self.s.port, self.token) is not None:
            return "already running"
        argv = [self.python, "-m", "piratebox", "--http", "--lan", "--no-console",
                "--http-port", str(self.s.port), "--token", self.token,
                "--to-fl", self.s.to_fl, "--from-fl", self.s.from_fl]
        if self.s.simulate:
            argv.append("--simulate")
        if self.s.ai:
            argv.append("--ai")
        if self.s.ble:
            argv.append("--ble")
        self.bridge = self.sys.start(argv, cwd=BRIDGE_DIR)
        if not self._wait(self.s.bridge_timeout, lambda: self.sys.status(self.s.port, self.token) is not None):
            raise StepFailed("PirateBox didn't start.",
                             f"Port {self.s.port} may be in use. Check the logs folder for errors.")
        return f"listening on port {self.s.port}"

    def start_fl(self) -> str:
        if self.s.simulate:
            return "skipped"
        if self.sys.process_running("FL64.exe") or self.sys.process_running("FL.exe"):
            return "already open"
        path = self.s.fl_path or self.sys.find_file(FL_GLOBS)
        if not path or not self.sys.exists(path):
            raise StepFailed("FL Studio not found.", "Install FL Studio, or pass --fl-path to FL64.exe.")
        self.sys.start([path])
        return "launched"

    def wait_ready(self) -> str:
        last = {}

        def ready():
            reply = self.sys.status(self.s.port, self.token)
            if reply:
                last.update(reply)
            return bool(reply and reply.get("connected"))

        if not self._wait(self.s.fl_timeout, ready):
            raise StepFailed("FL Studio isn't answering PirateBox.",
                             "In FL Studio: Options > MIDI settings, enable 'PirateBox ToFL' with controller "
                             "type 'PirateBox Controller'. " + (last.get("message") or ""))
        tempo = (last.get("state") or {}).get("tempo")
        return f"tempo {tempo:g} BPM" if isinstance(tempo, (int, float)) else ""

    def supervise(self, stop: Callable[[], bool] = lambda: False) -> None:
        """Keep the bridge alive for the rest of the session."""
        while not stop():
            if self.bridge is not None and self.bridge.poll() is not None:
                try:
                    self.start_bridge()
                except StepFailed:
                    pass
            self.sys.sleep(5)


# ---- front ends ----------------------------------------------------------------------------------

def run_headless(launcher: Launcher) -> int:
    ok = launcher.run()
    print("PirateBox is ready." if ok else "PirateBox could not finish starting.")
    if ok and launcher.bridge is not None:
        try:
            launcher.supervise()
        except KeyboardInterrupt:
            pass
    return 0 if ok else 1


def run_splash(settings: Settings) -> int:  # pragma: no cover - needs a display (Windows appliance)
    import threading
    import tkinter as tk

    root = tk.Tk()
    root.title("PirateBox")
    root.configure(bg="#0e0f11", cursor="none")
    root.attributes("-fullscreen", True)
    root.attributes("-topmost", True)
    root.bind("<Escape>", lambda e: root.destroy())

    frame = tk.Frame(root, bg="#0e0f11")
    frame.place(relx=0.5, rely=0.45, anchor="center")
    try:
        logo = tk.PhotoImage(file=str(LOGO))
        tk.Label(frame, image=logo, bg="#0e0f11").pack(pady=(0, 30))
    except tk.TclError:
        tk.Label(frame, text="PIRATE BOX", fg="white", bg="#0e0f11", font=("Segoe UI", 48, "bold")).pack(pady=(0, 30))

    rows = {}
    marks = {"run": ("…", "#8b919a"), "ok": ("✓", "#3ecf8e"), "skip": ("–", "#8b919a"), "fail": ("✕", "#ff5c5c")}
    for name in Launcher.STEPS:
        row = tk.Frame(frame, bg="#0e0f11")
        row.pack(anchor="w", pady=3)
        mark = tk.Label(row, text="·", width=2, fg="#2b2f35", bg="#0e0f11", font=("Segoe UI", 16, "bold"))
        mark.pack(side="left")
        label = tk.Label(row, text=name, fg="#8b919a", bg="#0e0f11", font=("Segoe UI", 16))
        label.pack(side="left")
        detail = tk.Label(row, text="", fg="#8b919a", bg="#0e0f11", font=("Segoe UI", 12))
        detail.pack(side="left", padx=12)
        rows[name] = (mark, label, detail)
    problem = tk.Label(frame, text="", fg="#ff5c5c", bg="#0e0f11", font=("Segoe UI", 13), wraplength=700, justify="left")
    problem.pack(pady=(24, 0), anchor="w")

    def report(step, state, text):
        def apply():
            mark, label, detail = rows[step]
            symbol, color = marks[state]
            mark.configure(text=symbol, fg=color)
            label.configure(fg="white" if state in ("run", "ok") else color)
            if state == "fail":
                problem.configure(text=text + "\n\nPress Esc to close this screen.")
                root.configure(cursor="")
            else:
                detail.configure(text=text)
        root.after(0, apply)

    launcher = Launcher(settings, report)

    def fade(alpha=1.0):
        if alpha <= 0:
            root.destroy()
            return
        root.attributes("-alpha", alpha)
        root.after(40, fade, alpha - 0.05)

    def work():
        if launcher.run():
            root.after(1500, fade)
            launcher.supervise()

    threading.Thread(target=work, daemon=True).start()
    root.mainloop()
    # Window gone: keep supervising the bridge until the user logs off.
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="piratebox.launcher", description="Start PirateBox and FL Studio at login.")
    p.add_argument("--headless", action="store_true", help="print progress instead of showing the splash screen")
    p.add_argument("--simulate", action="store_true", help="simulated FL Studio; skips loopMIDI and FL")
    p.add_argument("--ai", action="store_true", help="start the bridge with the Claude interpreter")
    p.add_argument("--ble", action="store_true", help="also let the phone connect over Bluetooth")
    p.add_argument("--fl-path", help="path to FL64.exe if it isn't in the default install folder")
    p.add_argument("--port", type=int, default=7142)
    args = p.parse_args(argv)
    settings = Settings(simulate=args.simulate, ai=args.ai, ble=args.ble, fl_path=args.fl_path, port=args.port)

    if args.headless:
        def report(step, state, detail):
            if state != "run":
                print(f"[{state.upper():4}] {step}" + (f": {detail}" if detail else ""), flush=True)
        return run_headless(Launcher(settings, report))
    return run_splash(settings)


if __name__ == "__main__":
    sys.exit(main())
