"""
Language-model providers. A provider takes a system prompt, a user message and a JSON schema,
and returns a dict that conforms to that schema. That's all the interpreter needs, so another
vendor (or a local model) can be dropped in by implementing JSONModelProvider.
"""

import json
from dataclasses import dataclass, field
from typing import Protocol

from ..errors import BridgeError, Status

DEFAULT_CLAUDE_MODEL = "claude-haiku-4-5"


@dataclass
class ProviderResult:
    data: dict
    details: dict = field(default_factory=dict)  # model, token usage... (journaled)


class JSONModelProvider(Protocol):
    name: str

    def complete_json(self, system: str, user: str, schema: dict) -> ProviderResult:
        """Raise BridgeError(Status.AI_UNAVAILABLE, ...) on any failure."""


class ClaudeProvider:
    """Anthropic Claude via structured outputs: the reply is guaranteed to match `schema`."""

    name = "claude"

    def __init__(self, model: str = DEFAULT_CLAUDE_MODEL, timeout: float = 8.0, max_retries: int = 1):
        import anthropic  # imported lazily so the bridge runs without the SDK when AI is off

        self._anthropic = anthropic
        self.model = model
        # Credentials: ANTHROPIC_API_KEY, or any source the SDK resolves (e.g. an `ant auth login` profile).
        # A voice command shouldn't hang: short timeout, one retry (the SDK retries 429/5xx/network).
        self.client = anthropic.Anthropic(timeout=timeout, max_retries=max_retries)

    def complete_json(self, system: str, user: str, schema: dict) -> ProviderResult:
        a = self._anthropic
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=512,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"format": {"type": "json_schema", "schema": schema}},
            )
        except a.AuthenticationError:
            raise _unavailable("Anthropic API key missing or invalid (set ANTHROPIC_API_KEY)")
        except a.PermissionDeniedError:
            raise _unavailable("Anthropic API key lacks permission for this model")
        except a.RateLimitError:
            raise _unavailable("AI service is rate-limiting requests, try again in a moment")
        except a.BadRequestError as e:
            raise _unavailable(f"AI request rejected: {e.message}")
        except a.APIStatusError as e:
            raise _unavailable(f"AI service error (HTTP {e.status_code})")
        except a.APITimeoutError:
            raise _unavailable("AI service timed out")
        except a.APIConnectionError:
            raise _unavailable("cannot reach the AI service (no internet?)")
        except TypeError:  # SDK raises this when no credentials can be resolved at all
            raise _unavailable("no Anthropic credentials configured (set ANTHROPIC_API_KEY)")

        details = {
            "provider": self.name,
            "model": response.model,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "stop_reason": response.stop_reason,
        }
        if response.stop_reason != "end_turn":
            raise _unavailable(f"AI stopped early ({response.stop_reason})")
        text = next((b.text for b in response.content if b.type == "text"), None)
        try:
            data = json.loads(text)
        except (TypeError, json.JSONDecodeError):
            raise _unavailable("AI returned malformed output")
        return ProviderResult(data=data, details=details)


def _unavailable(message):
    return BridgeError(Status.AI_UNAVAILABLE, f"AI interpreter: {message}. Exact commands like SET_TEMPO 142 still work.")
