"""
Run PirateBox.

    python -m piratebox                    interactive console, real FL Studio
    python -m piratebox --simulate         interactive console, simulated FL Studio
    python -m piratebox --http             also serve the button test client on http://127.0.0.1:7142
    python -m piratebox --http --lan       ...reachable from a phone on the same network
    python -m piratebox --http --lan --ble ...and over Bluetooth, for when there is no Wi-Fi
    python -m piratebox --ai               also understand conversational text via Claude Haiku
    python -m piratebox --once "SET_TEMPO 142"
    python -m piratebox --list-ports
"""

import argparse
import json
import logging
import os
import sys
import threading
from pathlib import Path

from . import __version__
from .commands import CommandRegistry
from .engine import CommandEngine
from .fl import wire
from .fl.controller import FLController
from .fl.link import FLLink
from .inputs.console import ConsoleInput
from .inputs.http import HttpInput
from .interpreters import ChainInterpreter, RuleInterpreter
from .journal import Journal

DEFAULT_LOG_DIR = Path(__file__).resolve().parents[2] / "logs"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="piratebox", description="PirateBox v" + __version__)
    p.add_argument("--to-fl", default="PirateBox ToFL", help="MIDI port the bridge sends on (FL's input)")
    p.add_argument("--from-fl", default="PirateBox FromFL", help="MIDI port the bridge listens on (FL's output)")
    p.add_argument("--timeout", type=float, default=2.0, help="seconds to wait for FL Studio to answer")
    p.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    p.add_argument("--simulate", action="store_true", help="use the built-in FL Studio simulator")
    p.add_argument("--once", metavar="COMMAND", help="run a single command, print JSON, exit (code 0 = success)")
    p.add_argument("--list-ports", action="store_true", help="list MIDI ports and exit")
    p.add_argument("--http", action="store_true", help="serve the button test client / HTTP API")
    p.add_argument("--http-port", type=int, default=7142)
    p.add_argument("--lan", action="store_true", help="listen on all interfaces (phone on same Wi-Fi), not just localhost")
    p.add_argument("--token", default=os.environ.get("PIRATEBOX_TOKEN"),
                   help="pairing token for HTTP clients (default: $PIRATEBOX_TOKEN, else the saved one)")
    p.add_argument("--new-token", action="store_true", help="generate a new saved pairing token (un-pairs all phones)")
    p.add_argument("--allow-origin", metavar="ORIGIN",
                   help="dev only: allow browser calls from this origin, e.g. http://localhost:8081 (Expo web)")
    p.add_argument("--ble", action="store_true", help="also accept the phone app over Bluetooth LE")
    p.add_argument("--ble-name", default="PirateBox", help="Bluetooth name the phone looks for")
    p.add_argument("--no-console", action="store_true", help="don't start the interactive console (use with --http)")
    p.add_argument("--ai", action="store_true",
                   help="use Claude to interpret conversational text that exact commands don't match")
    p.add_argument("--ai-model", default=None, help="Claude model id (default: claude-haiku-4-5)")
    p.add_argument("-v", "--verbose", action="store_true", help="print every pipeline stage")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.list_ports:
        from .fl.ports import list_ports
        print(json.dumps(list_ports(), indent=2))
        return 0

    if args.simulate:
        from .fl.simulator import SimulatedPortPair
        ports = SimulatedPortPair()
    else:
        from .fl.ports import MidoPortPair
        ports = MidoPortPair(args.to_fl, args.from_fl)

    def on_event(frame):
        if frame.op == wire.EV_HELLO:
            logging.getLogger("piratebox").info(
                "FL Studio script said hello (FL API v%d)", wire.from_u21(frame.payload[0:3]))

    link = FLLink(ports, timeout=args.timeout, on_event=on_event)
    registry = CommandRegistry()
    interpreter = RuleInterpreter()
    ai_label = "off (exact commands only; enable with --ai)"
    if args.ai:
        try:
            from .ai.interpreter import AIInterpreter
            from .ai.provider import DEFAULT_CLAUDE_MODEL, ClaudeProvider
            model = args.ai_model or DEFAULT_CLAUDE_MODEL
            interpreter = ChainInterpreter(RuleInterpreter(), AIInterpreter(ClaudeProvider(model), registry))
            ai_label = f"Claude {model} (exact commands are handled locally first)"
            if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
                ai_label += "  [warning: ANTHROPIC_API_KEY not set]"
        except ImportError:
            print("--ai needs the Anthropic SDK:  pip install -r bridge/requirements.txt")
            return 1
    engine = CommandEngine(FLController(link), registry, interpreter, Journal(args.log_dir))

    http = None
    ble = None
    try:
        if args.once:
            result = engine.handle_text(args.once, source="cli")
            print(json.dumps(result, indent=2))
            return 0 if result["success"] else 1

        status = engine.handle_text("status", source="startup")
        mode = "SIMULATED FL Studio" if args.simulate else f"ports '{args.to_fl}' -> FL -> '{args.from_fl}'"
        print(f"PirateBox v{__version__}  [{mode}]")
        if status["success"]:
            print(f"FL Studio READY  (tempo {status['state']['tempo']:g} BPM)")
        else:
            print(f"FL Studio NOT READY: {status['status']} - {status['message']}")
            print("Commands will retry the connection automatically.")
        print(f"AI interpreter: {ai_label}")
        print(f"Logging to {args.log_dir}")

        token = None
        if args.http or args.ble:
            from .pairing import load_or_create_token
            token = args.token or load_or_create_token(rotate=args.new_token)
        if args.ble:
            from .inputs.ble import BleInput
            ble = BleInput(engine, token, device_name=args.ble_name)
            try:
                ble.start()
                print(f"Bluetooth: advertising as '{args.ble_name}'")
            except (RuntimeError, ImportError) as e:
                print(f"Bluetooth not available ({e}). Continuing without it.")
                ble = None
        if args.http:
            from .pairing import qr_terminal
            http = HttpInput(engine, host="0.0.0.0" if args.lan else "127.0.0.1",
                             port=args.http_port, token=token, allow_origin=args.allow_origin,
                             ble_name=args.ble_name if ble else None)
            try:
                http.start()
            except OSError as e:
                print(f"Could not start HTTP test client on port {args.http_port}: {e}")
                return 1
            print("Test client:")
            for url in http.urls():
                print(f"  {url}")
            if args.lan:
                print("  (LAN mode: anyone on this network with the token can control FL Studio)")
                uri = http.pairing_uri()
                if uri:
                    print("\nPair the phone app by scanning this code (also at http://127.0.0.1:"
                          f"{http.bound_port}/pair on this computer):")
                    qr_terminal(uri)
                    print(f"  {uri}")
        print()

        if args.no_console:
            if not http and not ble:
                print("--no-console needs --http or --ble, otherwise there is no input.")
                return 1
            try:
                threading.Event().wait()
            except KeyboardInterrupt:
                pass
        else:
            ConsoleInput(engine).run()
        return 0
    finally:
        if http:
            http.stop()
        if ble:
            ble.stop()
        link.close()


if __name__ == "__main__":
    sys.exit(main())
