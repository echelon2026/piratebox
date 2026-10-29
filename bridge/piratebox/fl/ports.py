"""
Port pairs: the physical transport under FLLink.

A port pair sends SysEx data to FL Studio and delivers SysEx data from it to a
callback. MidoPortPair talks to real (virtual) MIDI ports; the simulator in
simulator.py provides an in-process pair for tests and --simulate.
"""

from typing import Callable, List, Protocol

from ..errors import BridgeError, Status

ReceiveCallback = Callable[[List[int]], None]


class PortPair(Protocol):
    is_open: bool

    def open(self, on_receive: ReceiveCallback) -> None: ...
    def send(self, data: List[int]) -> None: ...
    def close(self) -> None: ...


def _find_port(wanted: str, available: List[str], direction: str) -> str:
    # Windows/loopMIDI append an index ("PirateBox ToFL 1"), macOS IAC prefixes
    # "IAC Driver", so match case-insensitively on substring.
    for name in available:
        if wanted.lower() in name.lower():
            return name
    raise BridgeError(
        Status.FL_NOT_CONNECTED,
        f"MIDI {direction} port '{wanted}' not found. Available: {available or 'none'}",
    )


class MidoPortPair:
    def __init__(self, to_fl: str, from_fl: str):
        self.to_fl_name = to_fl
        self.from_fl_name = from_fl
        self._out = None
        self._in = None

    @property
    def is_open(self) -> bool:
        return self._out is not None and self._in is not None

    def open(self, on_receive: ReceiveCallback) -> None:
        import mido  # imported lazily so --simulate works without MIDI libs

        self.close()
        out_name = _find_port(self.to_fl_name, mido.get_output_names(), "output")
        in_name = _find_port(self.from_fl_name, mido.get_input_names(), "input")

        def callback(msg):
            if msg.type == "sysex":
                on_receive(list(msg.data))

        try:
            self._out = mido.open_output(out_name)
            self._in = mido.open_input(in_name, callback=callback)
        except Exception as e:
            self.close()
            raise BridgeError(Status.FL_NOT_CONNECTED, f"could not open MIDI ports: {e}") from e

    def send(self, data: List[int]) -> None:
        import mido

        self._out.send(mido.Message("sysex", data=data))

    def close(self) -> None:
        for port in (self._in, self._out):
            if port is not None:
                try:
                    port.close()
                except Exception:
                    pass
        self._in = self._out = None


def list_ports():
    import mido

    return {"inputs": mido.get_input_names(), "outputs": mido.get_output_names()}
