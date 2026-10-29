"""
InputSource: the plug-in point for every way commands can arrive.

A source only moves messages: it hands text or structured commands to the
engine and delivers the result dict back over its own transport. It never
talks to FL Studio directly. Future sources (WebSocket, named pipe,
Bluetooth, phone app) implement this same interface.
"""

from abc import ABC, abstractmethod

from ..engine import CommandEngine


class InputSource(ABC):
    name = "unknown"

    def __init__(self, engine: CommandEngine):
        self.engine = engine

    @abstractmethod
    def run(self) -> None:
        """Serve until stopped. Must not let one bad message kill the loop."""
