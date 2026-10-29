"""Status codes (mirrors protocol/commands.json 'status_codes') and bridge exceptions."""

from enum import Enum


class Status(str, Enum):
    OK = "OK"
    PARSE_FAILED = "PARSE_FAILED"
    INVALID_MESSAGE = "INVALID_MESSAGE"
    UNKNOWN_COMMAND = "UNKNOWN_COMMAND"
    INVALID_PARAMETERS = "INVALID_PARAMETERS"
    OUT_OF_RANGE = "OUT_OF_RANGE"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    FL_NOT_CONNECTED = "FL_NOT_CONNECTED"
    FL_TIMEOUT = "FL_TIMEOUT"
    FL_REJECTED = "FL_REJECTED"
    FL_EXEC_ERROR = "FL_EXEC_ERROR"
    FL_VERSION_MISMATCH = "FL_VERSION_MISMATCH"
    NOT_CONFIRMED = "NOT_CONFIRMED"
    NO_EFFECT = "NO_EFFECT"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    AI_UNAVAILABLE = "AI_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class BridgeError(Exception):
    """Any failure that maps to a well-defined status code."""

    def __init__(self, status: Status, message: str):
        super().__init__(message)
        self.status = status
        self.message = message
