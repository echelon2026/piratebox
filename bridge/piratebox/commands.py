"""
Command whitelist and strict validation, driven by protocol/commands.json.

Anything that reaches the engine has passed through validate(): known command,
exact parameter set, correct types, values within range.
"""

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from .errors import BridgeError, Status

DEFAULT_SCHEMA = Path(__file__).resolve().parents[2] / "protocol" / "commands.json"

_ALLOWED_KEYS = {"command", "parameters", "confirmed", "request_id"}


@dataclass(frozen=True)
class Command:
    name: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    confirmed: bool = False
    request_id: Optional[str] = None

    def to_dict(self):
        return {"command": self.name, "parameters": dict(self.parameters)}


class CommandRegistry:
    def __init__(self, schema_path: Path = DEFAULT_SCHEMA):
        with open(schema_path, encoding="utf-8") as f:
            schema = json.load(f)
        self.commands: Dict[str, dict] = schema["commands"]
        self.planned = set(schema.get("planned", []))
        self.status_codes = schema.get("status_codes", {})

    def spec(self, name: str) -> dict:
        return self.commands[name]

    def validate(self, raw: Any) -> Command:
        if not isinstance(raw, dict):
            raise BridgeError(Status.INVALID_MESSAGE, "command must be a JSON object")
        extra = set(raw) - _ALLOWED_KEYS
        if extra:
            raise BridgeError(Status.INVALID_MESSAGE, f"unexpected fields: {sorted(extra)}")

        name = raw.get("command")
        if not isinstance(name, str):
            raise BridgeError(Status.INVALID_MESSAGE, "'command' must be a string")
        if name not in self.commands:
            hint = " (planned, not yet enabled)" if name in self.planned else ""
            raise BridgeError(Status.UNKNOWN_COMMAND, f"unknown command '{name}'{hint}")

        params = raw.get("parameters", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise BridgeError(Status.INVALID_PARAMETERS, "'parameters' must be an object")

        confirmed = raw.get("confirmed", False)
        if not isinstance(confirmed, bool):
            raise BridgeError(Status.INVALID_MESSAGE, "'confirmed' must be a boolean")

        request_id = raw.get("request_id")
        if request_id is not None and (not isinstance(request_id, str) or len(request_id) > 64):
            raise BridgeError(Status.INVALID_MESSAGE, "'request_id' must be a string of at most 64 chars")

        spec_params = self.commands[name].get("parameters", {})
        missing = set(spec_params) - set(params)
        unexpected = set(params) - set(spec_params)
        if missing or unexpected:
            parts = []
            if missing:
                parts.append(f"missing {sorted(missing)}")
            if unexpected:
                parts.append(f"unexpected {sorted(unexpected)}")
            raise BridgeError(Status.INVALID_PARAMETERS, f"{name}: " + ", ".join(parts))

        clean = {key: _check_param(name, key, params[key], rule) for key, rule in spec_params.items()}
        return Command(name=name, parameters=clean, confirmed=confirmed, request_id=request_id)


def _check_param(command, key, value, rule):
    kind = rule["type"]
    if kind in ("number", "integer"):
        # bool is a subclass of int in Python; never accept it as a number.
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise BridgeError(Status.INVALID_PARAMETERS, f"{command}.{key} must be a number")
        if kind == "integer":
            if value != int(value):
                raise BridgeError(Status.INVALID_PARAMETERS, f"{command}.{key} must be a whole number")
            value = int(value)
        elif "decimals" in rule:
            value = round(float(value), rule["decimals"])
        lo, hi = rule.get("minimum"), rule.get("maximum")
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            raise BridgeError(Status.OUT_OF_RANGE, f"{command}.{key}={value} is outside {lo}..{hi}")
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return value
    if kind == "boolean":
        if not isinstance(value, bool):
            raise BridgeError(Status.INVALID_PARAMETERS, f"{command}.{key} must be true/false")
        return value
    raise BridgeError(Status.INTERNAL_ERROR, f"schema error: unsupported type '{kind}'")
