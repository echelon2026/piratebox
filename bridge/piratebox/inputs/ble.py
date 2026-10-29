"""
Bluetooth LE input (Phase 11): the phone talks to PirateBox without Wi-Fi.

The computer is a BLE peripheral advertising one GATT service with two characteristics:

    RX  (phone -> computer, write)    request bytes
    TX  (computer -> phone, notify)   reply bytes

Both directions carry the same JSON messages as HTTP (protocol/MESSAGES.md), one per line: UTF-8 JSON
terminated by "\\n", split into chunks that fit the link's MTU. BLE has no headers, so every request
also carries the pairing token:  {"token": "...", "type": "status"}. A wrong token gets
{"type": "command_result", "success": false, "status": "UNAUTHORIZED"} and nothing else.

Anyone nearby can see the service, and BLE traffic isn't encrypted here, so the token is the only lock:
the same trust level as the HTTP API on a home Wi-Fi network.
"""

import asyncio
import hmac
import json
import logging
import queue
import threading
from typing import Callable, List, Optional

from ..engine import CommandEngine
from .base import InputSource
from .messages import handle_message

log = logging.getLogger("piratebox.ble")

SERVICE_UUID = "6e3a0001-2c55-4b1e-9f51-7069726174b0"
RX_UUID = "6e3a0002-2c55-4b1e-9f51-7069726174b0"
TX_UUID = "6e3a0003-2c55-4b1e-9f51-7069726174b0"

CHUNK_SIZE = 180  # fits the default iOS MTU (185) minus ATT overhead
MAX_LINE = 8192  # a request longer than this is garbage; drop it


class FrameAssembler:
    """Collects written chunks and yields complete lines. Works on bytes so a multi-byte
    UTF-8 character split across two chunks is reassembled correctly."""

    def __init__(self, max_line: int = MAX_LINE):
        self.max_line = max_line
        self._buf = bytearray()

    def feed(self, data: bytes) -> List[str]:
        self._buf.extend(data)
        lines = []
        while b"\n" in self._buf:
            raw, _, rest = bytes(self._buf).partition(b"\n")
            self._buf = bytearray(rest)
            if raw.strip():
                lines.append(raw.decode("utf-8", errors="replace"))
        if len(self._buf) > self.max_line:
            log.warning("dropping oversized BLE frame (%d bytes)", len(self._buf))
            self._buf.clear()
        return lines


def encode_frames(message: dict, size: int = CHUNK_SIZE) -> List[bytes]:
    data = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
    return [data[i:i + size] for i in range(0, len(data), size)]


def handle_line(engine: CommandEngine, token: str, line: str) -> dict:
    """One request line in, one reply dict out. Never raises."""
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        return {"type": "command_result", "success": False, "status": "INVALID_MESSAGE", "message": "not JSON"}
    if not isinstance(msg, dict):
        return {"type": "command_result", "success": False, "status": "INVALID_MESSAGE",
                "message": "message must be a JSON object"}
    supplied = msg.pop("token", "")
    if not isinstance(supplied, str) or not hmac.compare_digest(supplied.encode(), token.encode()):
        return {"type": "command_result", "success": False, "status": "UNAUTHORIZED",
                "message": "Pairing was rejected. Scan the code again."}
    try:
        return handle_message(engine, msg, source="ble")
    except Exception as e:  # one bad message must not kill the link
        log.exception("BLE message failed")
        return {"type": "command_result", "success": False, "status": "INTERNAL_ERROR", "message": repr(e)}


class BleInput(InputSource):
    name = "ble"

    def __init__(self, engine: CommandEngine, token: str, device_name: str = "PirateBox"):
        super().__init__(engine)
        self.token = token
        self.device_name = device_name
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._server = None
        self._assembler = FrameAssembler()
        self._requests: "queue.Queue[Optional[str]]" = queue.Queue()
        self._ready = threading.Event()
        self._error: Optional[BaseException] = None

    # -- InputSource ---------------------------------------------------------------

    def run(self) -> None:
        self.start()
        threading.Event().wait()

    def start(self, timeout: float = 15.0) -> None:
        """Start advertising on a background thread. Raises if Bluetooth isn't usable."""
        threading.Thread(target=self._run_loop, name="ble-input", daemon=True).start()
        threading.Thread(target=self._worker, name="ble-worker", daemon=True).start()
        if not self._ready.wait(timeout):
            raise RuntimeError("Bluetooth did not start in time")
        if self._error:
            raise RuntimeError(f"Bluetooth unavailable: {self._error}")

    def stop(self) -> None:
        self._requests.put(None)
        if self._loop and self._server:
            fut = asyncio.run_coroutine_threadsafe(self._server.stop(), self._loop)
            try:
                fut.result(5)
            except Exception:
                pass
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

    # -- internals -----------------------------------------------------------------

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        except BaseException as e:  # missing adapter, no permission, unsupported OS...
            self._error = e
            self._ready.set()
            return
        self._ready.set()
        self._loop.run_forever()

    async def _serve(self) -> None:
        from bless import BlessServer, GATTAttributePermissions, GATTCharacteristicProperties

        server = BlessServer(name=self.device_name, loop=self._loop)
        server.write_request_func = self._on_write
        server.read_request_func = lambda characteristic, **kwargs: characteristic.value
        gatt = {
            SERVICE_UUID: {
                RX_UUID: {
                    "Properties": GATTCharacteristicProperties.write | GATTCharacteristicProperties.write_without_response,
                    "Permissions": GATTAttributePermissions.writeable,
                    "Value": None,
                },
                TX_UUID: {
                    "Properties": GATTCharacteristicProperties.notify | GATTCharacteristicProperties.read,
                    "Permissions": GATTAttributePermissions.readable,
                    "Value": None,
                },
            }
        }
        await server.add_gatt(gatt)
        await server.start()
        self._server = server
        log.info("advertising BLE service %s as %r", SERVICE_UUID, self.device_name)

    def _on_write(self, characteristic, value, **kwargs) -> None:
        # Runs on the BLE event loop: only buffer here; FL calls block, so they run on the worker.
        if str(characteristic.uuid).lower() != RX_UUID:
            return
        for line in self._assembler.feed(bytes(value)):
            self._requests.put(line)

    def _worker(self) -> None:
        while True:
            line = self._requests.get()
            if line is None:
                return
            reply = handle_line(self.engine, self.token, line)
            if self._loop:
                asyncio.run_coroutine_threadsafe(self._notify(encode_frames(reply)), self._loop)

    async def _notify(self, frames: List[bytes]) -> None:
        characteristic = self._server.get_characteristic(TX_UUID)
        for frame in frames:
            characteristic.value = bytearray(frame)
            self._server.update_value(SERVICE_UUID, TX_UUID)
            await asyncio.sleep(0.005)  # let the stack flush; back-to-back notifies can be dropped
