"""
FLLink: request/response over a PortPair.

One request in flight at a time (FL executes scripts on its UI thread, so
pipelining buys nothing). Each request carries a 7-bit sequence number and the
matching response is awaited with a timeout. Ports are (re)opened lazily, so
if loopMIDI or FL restarts, the next command reconnects automatically.
"""

import itertools
import logging
import threading
from typing import Callable, Optional

from ..errors import BridgeError, Status
from . import wire
from .ports import PortPair

log = logging.getLogger("piratebox.link")


class FLLink:
    def __init__(self, ports: PortPair, timeout: float = 2.0,
                 on_event: Optional[Callable[[wire.Frame], None]] = None):
        self.ports = ports
        self.timeout = timeout
        self.on_event = on_event
        self._request_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending = {}  # seq -> [threading.Event, Frame | None, op]
        self._seq = itertools.cycle(range(128))

    def connect(self) -> None:
        self.ports.open(self._on_receive)
        log.info("MIDI link ports open")

    def close(self) -> None:
        self.ports.close()

    def request(self, op: int, payload=()) -> wire.Frame:
        with self._request_lock:
            if not self.ports.is_open:
                self.connect()
            seq = next(self._seq)
            slot = [threading.Event(), None, op]
            with self._pending_lock:
                self._pending[seq] = slot
            try:
                try:
                    self.ports.send(wire.encode(wire.MT_REQUEST, seq, op, payload))
                except BridgeError:
                    raise
                except Exception as e:
                    self.ports.close()  # force a reopen on the next request
                    raise BridgeError(Status.FL_NOT_CONNECTED, f"MIDI send failed: {e}") from e
                if not slot[0].wait(self.timeout):
                    raise BridgeError(
                        Status.FL_TIMEOUT,
                        f"FL Studio did not respond within {self.timeout:.1f}s "
                        "(is FL running with the PirateBox controller script active?)",
                    )
                return slot[1]
            finally:
                with self._pending_lock:
                    self._pending.pop(seq, None)

    def _on_receive(self, data) -> None:
        try:
            frame = wire.decode(data)
        except wire.WireError:
            return  # someone else's sysex
        if frame.msg_type == wire.MT_RESPONSE:
            with self._pending_lock:
                slot = self._pending.get(frame.seq)
            if slot is None or slot[2] != frame.op:
                log.warning("discarding stale/unmatched response seq=%d op=0x%02X", frame.seq, frame.op)
                return
            slot[1] = frame
            slot[0].set()
        elif frame.msg_type == wire.MT_EVENT and self.on_event:
            try:
                self.on_event(frame)
            except Exception:
                log.exception("event handler failed")
