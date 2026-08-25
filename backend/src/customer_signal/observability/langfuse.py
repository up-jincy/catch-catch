"""Langfuse tracing helpers."""

from __future__ import annotations

import json
import os
import re
import sys
import threading
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
_CLIENT: Any | None = None
_CLIENT_LOCK = threading.Lock()


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

        if _get_client() is None:
            return None
        return CallbackHandler(public_key=required[0])
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


@dataclass(slots=True)
class _NoOpObservation:
    def update(self, *, output: Any) -> None:
        del output


@dataclass(slots=True)
class _SafeObservation:
    observation: Any

    def update(self, *, output: Any) -> None:
        try:
            self.observation.update(output=sanitize_trace_value(output))
        except Exception:
            return


@contextmanager
def public_observation(
    *,
    name: str,
    stage: str,
    input: dict[str, Any],
) -> Iterator[_NoOpObservation | _SafeObservation]:
    """Record a public server-owned operation without affecting analysis results."""

    context = _CURRENT_RUN.get()
    client = _get_client()
    if context is None or client is None:
        yield _NoOpObservation()
        return

    metadata = {
        "provider": "server",
        "stage": stage,
        "run_id": context.run_id,
        "run_kind": context.run_kind,
        "enabled_sources": ",".join(context.source_ids),
        "langfuse_session_id": context.run_id,
        "langfuse_tags": ["customer-signal", "server", stage, context.run_kind],
    }
    try:
        manager = client.start_as_current_observation(
            name=name,
            as_type="tool",
            input=sanitize_trace_value(input),
            metadata=metadata,
        )
        observation = manager.__enter__()
    except Exception:
        yield _NoOpObservation()
        return

    try:
        yield _SafeObservation(observation)
    except BaseException:
        error = sys.exc_info()
        try:
            manager.__exit__(*error)
        except Exception:
            pass
        raise
    else:
        try:
            manager.__exit__(None, None, None)
        except Exception:
            pass


def flush_langfuse() -> None:
    """Flush the existing client, swallowing observability-only failures."""

    client = _CLIENT
    if client is None:
        return
    try:
        client.flush()
    except Exception:
        return


def _get_client() -> Any | None:
    global _CLIENT

    if _CLIENT is not None:
        return _CLIENT
    required = (
        os.getenv("LANGFUSE_PUBLIC_KEY", "").strip(),
        os.getenv("LANGFUSE_SECRET_KEY", "").strip(),
        os.getenv("LANGFUSE_BASE_URL", "").strip(),
    )
    if not all(required):
        return None

    with _CLIENT_LOCK:
        if _CLIENT is not None:
            return _CLIENT
        try:
            from langfuse import Langfuse

            _CLIENT = Langfuse(mask_otel_spans=_mask_otel_spans)
        except Exception:
            return None
    return _CLIENT


def _mask_otel_spans(params: Any) -> Any:
    from langfuse.types import MaskOtelSpansResult, OtelSpanPatch

    patches = {}
    for identifier, span in params.spans.items():
        attributes = {
            key: _sanitize_otel_attribute(key, value)
            for key, value in span.attributes.items()
        }
        patches[identifier] = OtelSpanPatch(set_attributes=attributes)
    return MaskOtelSpansResult(span_patches=patches)


def _sanitize_otel_attribute(key: str, value: Any) -> Any:
    if any(part in key.lower() for part in _SENSITIVE_KEY_PARTS):
        return _REDACTED
    if isinstance(value, str):
        sanitized = sanitize_trace_value(value)
        if isinstance(sanitized, str):
            return sanitized
        return json.dumps(sanitized, ensure_ascii=False, sort_keys=True)
    if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
        return [str(sanitize_trace_value(item)) for item in value]
    return value


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
