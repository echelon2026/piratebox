"""
Client message envelope, shared by every remote input source (HTTP now; WebSocket/Bluetooth later).
Spec: protocol/MESSAGES.md.

    {"type": "text", "text": "set the tempo to 142"}          (alias: "voice_command")
    {"type": "command", "command": "set_tempo", "parameters": {"bpm": 142}}
    {"type": "status"}

Replies are {"type": "command_result", ...engine result} or {"type": "status", ...}.
"""

from ..engine import CommandEngine

MAX_TEXT_LENGTH = 500


def handle_message(engine: CommandEngine, msg, source: str) -> dict:
    if not isinstance(msg, dict):
        return _error("message must be a JSON object")
    kind = msg.get("type")

    if kind in ("text", "voice_command"):
        text = msg.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_LENGTH:
            return _error(f"'text' must be a non-empty string of at most {MAX_TEXT_LENGTH} chars")
        return {"type": "command_result", **engine.handle_text(text, source=source)}

    if kind == "command":
        raw = {k: v for k, v in msg.items() if k != "type"}
        return {"type": "command_result", **engine.handle_command(raw, source=source)}

    if kind == "status":
        return {"type": "status", **engine.probe()}

    return _error(f"unknown message type {kind!r}")


def _error(message):
    return {"type": "command_result", "success": False, "status": "INVALID_MESSAGE", "message": message}
