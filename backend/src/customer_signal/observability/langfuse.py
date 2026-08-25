"""Langfuse tracing helpers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Any, Iterator, Literal


_REDACTED = "[REDACTED]"
_PRIVATE_AGENT_MESSAGE_REDACTED = "[PRIVATE_AGENT_MESSAGE_REDACTED]"
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "password",
    "public_key",
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
    trace_id: str = field(init=False)
    parent_observation_id: str | None = None

    def __post_init__(self) -> None:
        compact_run_id = self.run_id.replace("-", "").lower()
        if len(compact_run_id) == 32 and all(
            character in "0123456789abcdef" for character in compact_run_id
        ):
            trace_id = compact_run_id
        else:
            trace_id = hashlib.sha256(self.run_id.encode()).hexdigest()[:32]
        object.__setattr__(self, "trace_id", trace_id)


_CURRENT_RUN: ContextVar[LangfuseRunContext | None] = ContextVar(
    "langfuse_run_context", default=None
)
_CLIENT: Any | None = None
_CLIENT_LOCK = threading.Lock()
_CURRENT_WORKFLOW: ContextVar[Any | None] = ContextVar(
    "langfuse_workflow_observation", default=None
)


@contextmanager
def bind_langfuse_run(context: LangfuseRunContext) -> Iterator[LangfuseRunContext]:
    """Open one parent workflow observation and bind all child work to its trace."""

    workflow = _start_workflow_observation(context)
    bound_context = (
        replace(context, parent_observation_id=workflow.id)
        if workflow is not None
        else context
    )
    run_token = _CURRENT_RUN.set(bound_context)
    workflow_token = _CURRENT_WORKFLOW.set(workflow)
    try:
        yield bound_context
    except BaseException as error:
        _update_observation(
            workflow,
            output={"status": "failed", "error_type": type(error).__name__},
        )
        raise
    finally:
        _CURRENT_WORKFLOW.reset(workflow_token)
        _CURRENT_RUN.reset(run_token)
        _end_observation(workflow)


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
        context = _CURRENT_RUN.get()

        class WorkflowCallbackHandler(CallbackHandler):
            def _parse_langfuse_trace_attributes(
                self,
                *,
                metadata: dict[str, Any] | None,
                tags: list[str] | None,
            ) -> dict[str, Any]:
                merged_metadata = dict(metadata or {})
                if context is not None:
                    merged_metadata.setdefault(
                        "langfuse_session_id", context.run_id
                    )
                    merged_metadata.setdefault(
                        "langfuse_trace_name", "customer_signal.turn"
                    )
                    merged_metadata.setdefault(
                        "langfuse_tags",
                        ["customer-signal", context.run_kind],
                    )
                return super()._parse_langfuse_trace_attributes(
                    metadata=merged_metadata,
                    tags=tags,
                )

        return WorkflowCallbackHandler(
            public_key=required[0],
            trace_context=_trace_context(context) if context is not None else None,
        )
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
                "langfuse_trace_name": "customer_signal.turn",
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


def update_langfuse_workflow(*, output: Any) -> None:
    """Attach a public terminal summary to the current workflow observation."""

    _update_observation(_CURRENT_WORKFLOW.get(), output=output)


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
        observation = client.start_observation(
            name=name,
            as_type="tool",
            trace_context=_trace_context(context),
            input=sanitize_trace_value(input),
            metadata=metadata,
        )
    except Exception:
        yield _NoOpObservation()
        return

    try:
        yield _SafeObservation(observation)
    finally:
        _end_observation(observation)


def _start_workflow_observation(context: LangfuseRunContext) -> Any | None:
    client = _get_client()
    if client is None:
        return None
    metadata = {
        "provider": "gemini",
        "stage": "turn",
        "run_id": context.run_id,
        "run_kind": context.run_kind,
        "enabled_sources": ",".join(context.source_ids),
        "langfuse_session_id": context.run_id,
        "langfuse_tags": ["customer-signal", "turn", context.run_kind],
    }
    try:
        return client.start_observation(
            name="customer_signal.turn",
            as_type="agent",
            trace_context={"trace_id": context.trace_id},
            input=sanitize_trace_value(
                {
                    "question": context.question,
                    "enabled_sources": list(context.source_ids),
                }
            ),
            metadata=metadata,
        )
    except Exception:
        return None


def _trace_context(context: LangfuseRunContext) -> dict[str, str]:
    trace_context = {"trace_id": context.trace_id}
    if context.parent_observation_id:
        trace_context["parent_span_id"] = context.parent_observation_id
    return trace_context


def _update_observation(observation: Any | None, *, output: Any) -> None:
    if observation is None:
        return
    try:
        observation.update(output=sanitize_trace_value(output))
    except Exception:
        return


def _end_observation(observation: Any | None) -> None:
    if observation is None:
        return
    try:
        observation.end()
    except Exception:
        return


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
        attributes["langfuse.trace.name"] = "customer_signal.turn"
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
