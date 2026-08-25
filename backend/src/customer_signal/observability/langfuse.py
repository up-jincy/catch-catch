"""Langfuse tracing helpers."""

from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator, Literal


_REDACTED = "[REDACTED]"
_PRIVATE_AGENT_MESSAGE_REDACTED = "[PRIVATE_AGENT_MESSAGE_REDACTED]"
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
)
_EMAIL_PATTERN = re.compile(
    r"(?<![A-Z0-9._%+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![A-Z0-9.-])",
    re.IGNORECASE,
)
_PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?82[- ]?)?0?1[016789][ -]?\d{3,4}[ -]?\d{4}(?!\d)")


@dataclass(frozen=True, slots=True)
class LangfuseRunContext:
    run_id: str
    run_kind: Literal["generic", "legacy"]
    question: str
    source_ids: tuple[str, ...]


_CURRENT_RUN: ContextVar[LangfuseRunContext | None] = ContextVar(
    "langfuse_run_context", default=None
)


@contextmanager
def bind_langfuse_run(context: LangfuseRunContext) -> Iterator[LangfuseRunContext]:
    """Bind public trace metadata to the current async execution context."""

    token = _CURRENT_RUN.set(context)
    try:
        yield context
    finally:
        _CURRENT_RUN.reset(token)


def _new_callback_handler() -> Any | None:
    """Build a callback only when a complete Langfuse configuration is present."""

    required = (
        os.getenv("LANGFUSE_PUBLIC_KEY", "").strip(),
        os.getenv("LANGFUSE_SECRET_KEY", "").strip(),
        os.getenv("LANGFUSE_BASE_URL", "").strip(),
    )
    if not all(required):
        return None

    try:
        from langfuse.langchain import CallbackHandler

        return CallbackHandler()
    except Exception:
        # Observability must never make the customer analysis fail.
        return None


def build_langfuse_config(*, run_name: str, provider: str, stage: str) -> dict[str, Any]:
    context = _CURRENT_RUN.get()
    handler = _new_callback_handler()
    metadata: dict[str, Any] = {"provider": provider, "stage": stage}
    tags = ["customer-signal", provider, stage]
    if context is not None:
        metadata.update(
            {
                "run_id": context.run_id,
                "run_kind": context.run_kind,
                "enabled_sources": ",".join(context.source_ids),
                "langfuse_session_id": context.run_id,
                "langfuse_tags": tags,
            }
        )

    return {
        "callbacks": [handler] if handler is not None else [],
        "run_name": run_name,
        "tags": tags,
        "metadata": metadata,
    }


def sanitize_trace_value(value: Any) -> Any:
    """Keep the public execution contract while removing secrets and private content."""

    if isinstance(value, dict):
        sanitized: dict[Any, Any] = {}
        for key, item in value.items():
            normalized_key = str(key).lower()
            if any(part in normalized_key for part in _SENSITIVE_KEY_PARTS):
                sanitized[key] = _REDACTED
            elif normalized_key == "messages" and isinstance(item, list):
                sanitized[key] = [_sanitize_message(message) for message in item]
            else:
                sanitized[key] = sanitize_trace_value(item)
        return sanitized

    if isinstance(value, list):
        return [sanitize_trace_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_trace_value(item) for item in value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                parsed = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                pass
            else:
                return json.dumps(
                    sanitize_trace_value(parsed), ensure_ascii=False, sort_keys=True
                )
        return _scrub_public_text(value)
    return value


def _sanitize_message(message: Any) -> Any:
    if not isinstance(message, dict):
        return sanitize_trace_value(message)

    role = str(message.get("role", "")).lower()
    sanitized = {key: sanitize_trace_value(value) for key, value in message.items()}
    if role != "user" and "content" in sanitized:
        sanitized["content"] = _PRIVATE_AGENT_MESSAGE_REDACTED
    return sanitized


def _scrub_public_text(value: str) -> str:
    value = _EMAIL_PATTERN.sub("[EMAIL_REDACTED]", value)
    return _PHONE_PATTERN.sub("[PHONE_REDACTED]", value)
