from __future__ import annotations

from types import SimpleNamespace

from customer_signal.observability.langfuse import (
    LangfuseRunContext,
    _mask_otel_spans,
    bind_langfuse_run,
    build_langfuse_config,
    public_observation,
    sanitize_trace_value,
)


class _FakeSpan:
    def __init__(self, span_id: str) -> None:
        self.id = span_id
        self.updates = []
        self.ended = False

    def update(self, **kwargs) -> None:
        self.updates.append(kwargs)

    def end(self) -> None:
        self.ended = True


class _FakeClient:
    def __init__(self) -> None:
        self.calls = []
        self.spans = []

    def start_observation(self, **kwargs):
        self.calls.append(kwargs)
        span = _FakeSpan(f"span-{len(self.spans) + 1}")
        self.spans.append(span)
        return span


def test_callback_config_is_empty_without_credentials(monkeypatch) -> None:
    for name in (
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)

    with bind_langfuse_run(
        LangfuseRunContext(
            run_id="run-public-1",
            run_kind="generic",
            question="합성 고객 Journey를 분석해줘.",
            source_ids=("search_history", "voc"),
        )
    ):
        config = build_langfuse_config(
            run_name="customer_signal.goal",
            provider="gemini",
            stage="goal",
        )

    assert config["callbacks"] == []
    assert config["metadata"]["langfuse_session_id"] == "run-public-1"
    assert config["metadata"]["langfuse_trace_name"] == "customer_signal.turn"
    assert config["metadata"]["run_kind"] == "generic"
    assert config["metadata"]["enabled_sources"] == "search_history,voc"


def test_nested_run_contexts_keep_separate_session_ids(monkeypatch) -> None:
    sentinel_handler = object()
    monkeypatch.setattr(
        "customer_signal.observability.langfuse._new_callback_handler",
        lambda: sentinel_handler,
    )
    first = LangfuseRunContext("run-1", "generic", "질문 1", ("voc",))
    second = LangfuseRunContext("run-2", "legacy", "질문 2", ("voc",))

    with bind_langfuse_run(first):
        first_config = build_langfuse_config(
            run_name="customer_signal.goal",
            provider="gemini",
            stage="goal",
        )
        with bind_langfuse_run(second):
            second_config = build_langfuse_config(
                run_name="customer_signal.agent",
                provider="gemini",
                stage="agent",
            )

    assert first_config["callbacks"] == [sentinel_handler]
    assert second_config["callbacks"] == [sentinel_handler]
    assert first_config["metadata"]["langfuse_session_id"] == "run-1"
    assert second_config["metadata"]["langfuse_session_id"] == "run-2"


def test_one_run_groups_workflow_model_and_tool_under_one_trace(monkeypatch) -> None:
    client = _FakeClient()
    for name in (
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_BASE_URL",
    ):
        monkeypatch.setenv(name, "configured-for-test")

    class FakeCallbackHandler:
        def __init__(self, *, public_key, trace_context) -> None:
            self.public_key = public_key
            self.trace_context = trace_context

        def _parse_langfuse_trace_attributes(self, *, metadata, tags):
            return {"metadata": metadata, "tags": tags}

    monkeypatch.setattr("langfuse.langchain.CallbackHandler", FakeCallbackHandler)
    monkeypatch.setattr(
        "customer_signal.observability.langfuse._get_client",
        lambda: client,
    )
    context = LangfuseRunContext(
        run_id="5d7b9f51-90a2-44a5-83d5-125b0909d4cb",
        run_kind="generic",
        question="반복 Journey를 보여줘.",
        source_ids=("search_history", "voc"),
    )

    with bind_langfuse_run(context) as bound:
        assert bound.trace_id == "5d7b9f5190a244a583d5125b0909d4cb"
        assert bound.parent_observation_id == "span-1"
        config = build_langfuse_config(
            run_name="customer_signal.goal",
            provider="gemini",
            stage="goal",
        )
        with public_observation(
            name="customer_signal.tool.match_sequence",
            stage="tool",
            input={"primitive": "match_sequence"},
        ) as observation:
            observation.update(output={"fact_id": "fact-match"})

    assert [call["name"] for call in client.calls] == [
        "customer_signal.turn",
        "customer_signal.tool.match_sequence",
    ]
    assert client.calls[0]["trace_context"] == {"trace_id": bound.trace_id}
    assert config["callbacks"][0].trace_context == {
        "trace_id": bound.trace_id,
        "parent_span_id": "span-1",
    }
    nested_attributes = config["callbacks"][0]._parse_langfuse_trace_attributes(
        metadata=None,
        tags=None,
    )
    assert nested_attributes["metadata"]["langfuse_trace_name"] == (
        "customer_signal.turn"
    )
    assert nested_attributes["metadata"]["langfuse_session_id"] == context.run_id
    assert client.calls[1]["trace_context"] == {
        "trace_id": bound.trace_id,
        "parent_span_id": "span-1",
    }
    assert all(span.ended for span in client.spans)


def test_sanitizer_keeps_public_flow_and_redacts_sensitive_values() -> None:
    value = {
        "question": "문의 고객 test@example.com을 찾아줘",
        "api_key": "private-key",
        "public_key": "public-project-key",
        "messages": [
            {"role": "user", "content": "Journey를 보여줘"},
            {"role": "assistant", "content": "private reasoning"},
        ],
        "plan": {"steps": [{"primitive": "match_sequence"}]},
    }

    masked = sanitize_trace_value(value)

    assert masked["api_key"] == "[REDACTED]"
    assert masked["public_key"] == "[REDACTED]"
    assert "test@example.com" not in masked["question"]
    assert masked["messages"][0]["content"] == "Journey를 보여줘"
    assert masked["messages"][1]["content"] == "[PRIVATE_AGENT_MESSAGE_REDACTED]"
    assert masked["plan"]["steps"][0]["primitive"] == "match_sequence"


def test_sanitizer_parses_and_masks_serialized_json() -> None:
    serialized = (
        '{"document":{"plan":{"steps":[{"primitive":"aggregate_events"}]},'
        '"secret_key":"must-not-leak"}}'
    )

    masked = sanitize_trace_value(serialized)

    assert "must-not-leak" not in masked
    assert "aggregate_events" in masked


def test_export_mask_keeps_one_stable_workflow_trace_name() -> None:
    params = SimpleNamespace(
        spans={
            "span-1": SimpleNamespace(
                attributes={
                    "langfuse.trace.name": "write_todos",
                    "langfuse.public_key": "project-public-key",
                }
            )
        }
    )

    masked = _mask_otel_spans(params)
    attributes = masked.span_patches["span-1"].set_attributes

    assert attributes["langfuse.trace.name"] == "customer_signal.turn"
    assert attributes["langfuse.public_key"] == "[REDACTED]"
