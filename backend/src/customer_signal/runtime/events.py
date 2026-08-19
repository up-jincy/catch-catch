"""Small, public runner events safe to translate to SSE."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


type RunnerEventType = Literal[
    "plan",
    "tool_started",
    "tool_completed",
    "validating",
    "result",
    "error",
    "fallback",
]


_FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "chain_of_thought",
        "internal_reasoning",
        "masked_customer_id",
        "messages",
        "prompt",
        "raw_fields",
        "reasoning",
        "records",
        "thoughts",
    }
)


def _assert_safe_keys(value: JsonValue) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = key.casefold().replace("-", "_").replace(" ", "_")
            if normalized in _FORBIDDEN_PAYLOAD_KEYS:
                raise ValueError(f"payload key is not public: {key}")
            _assert_safe_keys(item)
    elif isinstance(value, list):
        for item in value:
            _assert_safe_keys(item)


class RunnerEvent(BaseModel):
    """A framework-neutral event without IDs, timestamps, or private model state."""

    model_config = ConfigDict(extra="forbid", strict=True)

    type: RunnerEventType
    payload: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_safe_payload(self) -> Self:
        _assert_safe_keys(self.payload)
        return self


__all__ = ["RunnerEvent", "RunnerEventType"]

