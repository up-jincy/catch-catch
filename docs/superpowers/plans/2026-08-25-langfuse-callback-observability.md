# Langfuse Callback Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** 범용 Gemini와 DeepAgent 분석의 발화, 계획, Tool 선택, 공개 입력과 검증된 출력을 Langfuse에서 Run 단위로 확인할 수 있게 합니다.

**Architecture:** Langfuse SDK와 LangChain `CallbackHandler`를 호출 config에 직접
연결합니다. 요청별 ContextVar가 공개 Run metadata를 전달하고, export 마스킹이 Key,
PII, 검증 전 Provider message를 제거합니다. 범용 서버 Primitive는 작은 공개
observation으로 Tool 입력과 검증된 Fact를 기록합니다.

**Tech Stack:** Python 3.12, Langfuse Python SDK v4, LangChain 1.3, DeepAgents 0.7, FastAPI, pytest, Ruff

---

## 파일 구조

- 생성: `backend/src/customer_signal/observability/__init__.py`
  - Backend 관측 API의 공개 export
- 생성: `backend/src/customer_signal/observability/langfuse.py`
  - Langfuse client, callback config, Run ContextVar, 마스킹, 공개 observation, flush
- 생성: `backend/tests/test_langfuse_observability.py`
  - 설정 누락, Run 격리, 마스킹, 공개 observation 단위 테스트
- 수정: `backend/src/customer_signal/runtime/coordinator.py`
  - Legacy와 Generic 실행 task에 Run context 연결, 종료 시 flush
- 수정: `backend/src/customer_signal/agent/generic_gemini.py`
  - 단계형 `ainvoke` config에 callback과 Langfuse metadata 추가
- 수정: `backend/src/customer_signal/agent/analysis_loop.py`
  - Primitive 실행 전후 공개 입력과 검증된 Fact observation 기록
- 수정: `backend/src/customer_signal/agent/gemini.py`
  - DeepAgent `ainvoke` config에 callback과 Run metadata 추가
- 수정: `backend/tests/test_generic_gemini.py`
  - 범용 단계 callback config 계약
- 수정: `backend/tests/test_gemini_adapter.py`
  - DeepAgent callback config 계약
- 수정: `backend/tests/test_analysis_loop.py`
  - Primitive 공개 observation 계약
- 수정: `backend/tests/test_dev_launcher.py`
  - Backend와 Frontend의 `LANGFUSE_*` 환경 격리 계약
- 수정: `backend/pyproject.toml`, `backend/uv.lock`
  - Langfuse SDK 고정 의존성
- 수정: `scripts/dev.sh`, `Makefile`
  - 선택한 env 우선 적용과 Frontend 비밀값 제거
- 수정: `README.md`
  - Langfuse 설정과 검증 방법

### Task 1: SDK와 Launcher 환경 격리

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/uv.lock`
- Modify: `backend/tests/test_dev_launcher.py`
- Modify: `scripts/dev.sh`
- Modify: `Makefile`

- [ ] **Step 1: Launcher 실패 테스트 작성**

`test_frontend_launchers_strip_provider_and_tracing_settings`에 다음 검증을 추가합니다.

```python
assert "-u LANGFUSE_SECRET_KEY" in launcher
assert "-u LANGFUSE_PUBLIC_KEY" in launcher
assert "-u LANGFUSE_BASE_URL" in launcher
assert "-u LANGFUSE_SECRET_KEY" in makefile
assert "-u LANGFUSE_PUBLIC_KEY" in makefile
assert "-u LANGFUSE_BASE_URL" in makefile
```

Backend env 격리 테스트를 추가합니다.

```python
def test_backend_launchers_prefer_selected_langfuse_env_file() -> None:
    launcher = _normalized(REPOSITORY_ROOT / "scripts" / "dev.sh")
    makefile = _normalized(REPOSITORY_ROOT / "Makefile")

    for variable in (
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_BASE_URL",
        "LANGFUSE_DEBUG",
    ):
        assert f"-u {variable}" in launcher
        assert f"-u {variable}" in makefile
```

- [ ] **Step 2: 실패 확인**

Run:

```bash
uv run --project backend pytest backend/tests/test_dev_launcher.py -q
```

Expected: `LANGFUSE_SECRET_KEY` assertion 실패

- [ ] **Step 3: SDK 추가와 Launcher 최소 구현**

Run:

```bash
uv add --project backend langfuse
```

`scripts/dev.sh`의 `env_isolation`과 `frontend_env_isolation`, Makefile의 fixture
`env_isolation`과 `serve-frontend` env 목록에 다음 값을 추가합니다.

```bash
-u LANGFUSE_SECRET_KEY
-u LANGFUSE_PUBLIC_KEY
-u LANGFUSE_BASE_URL
-u LANGFUSE_DEBUG
-u LANGFUSE_TRACING_ENVIRONMENT
-u LANGFUSE_RELEASE
```

- [ ] **Step 4: Launcher 테스트 통과 확인**

Run:

```bash
uv run --project backend pytest backend/tests/test_dev_launcher.py -q
uv run --project backend python -c "from langfuse.langchain import CallbackHandler; print(CallbackHandler.__name__)"
```

Expected: 전체 PASS와 `CallbackHandler`

- [ ] **Step 5: 커밋**

```bash
git add backend/pyproject.toml backend/uv.lock backend/tests/test_dev_launcher.py scripts/dev.sh Makefile
git commit -m "chore: (observability) Langfuse SDK와 환경 격리 추가"
```

### Task 2: 요청 Context, callback config와 마스킹

**Files:**
- Create: `backend/src/customer_signal/observability/__init__.py`
- Create: `backend/src/customer_signal/observability/langfuse.py`
- Create: `backend/tests/test_langfuse_observability.py`

- [ ] **Step 1: Context와 fail-open 실패 테스트 작성**

```python
from customer_signal.observability.langfuse import (
    LangfuseRunContext,
    bind_langfuse_run,
    build_langfuse_config,
)


def test_callback_config_is_empty_without_credentials(monkeypatch):
    for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL"):
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


def test_nested_async_runs_keep_separate_session_ids(monkeypatch):
    monkeypatch.setattr(
        "customer_signal.observability.langfuse._new_callback_handler",
        lambda: object(),
    )
    first = LangfuseRunContext("run-1", "generic", "질문 1", ("voc",))
    second = LangfuseRunContext("run-2", "legacy", "질문 2", ("voc",))

    with bind_langfuse_run(first):
        first_config = build_langfuse_config(
            run_name="customer_signal.goal", provider="gemini", stage="goal"
        )
        with bind_langfuse_run(second):
            second_config = build_langfuse_config(
                run_name="customer_signal.agent", provider="gemini", stage="agent"
            )

    assert first_config["metadata"]["langfuse_session_id"] == "run-1"
    assert second_config["metadata"]["langfuse_session_id"] == "run-2"
```

- [ ] **Step 2: 마스킹 실패 테스트 작성**

```python
from customer_signal.observability.langfuse import sanitize_trace_value


def test_sanitizer_keeps_public_flow_and_redacts_secrets_and_private_messages():
    value = {
        "question": "문의 고객 test@example.com을 찾아줘",
        "api_key": "private-key",
        "messages": [
            {"role": "user", "content": "Journey를 보여줘"},
            {"role": "assistant", "content": "private reasoning"},
        ],
        "plan": {"steps": [{"primitive": "match_sequence"}]},
    }

    masked = sanitize_trace_value(value)

    assert masked["api_key"] == "[REDACTED]"
    assert "test@example.com" not in masked["question"]
    assert masked["messages"][0]["content"] == "Journey를 보여줘"
    assert masked["messages"][1]["content"] == "[PRIVATE_AGENT_MESSAGE_REDACTED]"
    assert masked["plan"]["steps"][0]["primitive"] == "match_sequence"
```

- [ ] **Step 3: 실패 확인**

Run:

```bash
uv run --project backend pytest backend/tests/test_langfuse_observability.py -q
```

Expected: 모듈이 없어 collection ERROR. 파일을 만든 뒤에는 구현 전 assertion FAIL로
바꿔 다시 실행합니다.

- [ ] **Step 4: 최소 Helper 구현**

`langfuse.py`에 다음 공개 API를 구현합니다.

```python
@dataclass(frozen=True, slots=True)
class LangfuseRunContext:
    run_id: str
    run_kind: Literal["generic", "legacy"]
    question: str
    source_ids: tuple[str, ...]


@contextmanager
def bind_langfuse_run(context: LangfuseRunContext):
    token = _ACTIVE_RUN.set(context)
    try:
        yield context
    finally:
        _ACTIVE_RUN.reset(token)


def build_langfuse_config(*, run_name: str, provider: str, stage: str) -> dict[str, Any]:
    context = _ACTIVE_RUN.get()
    tags = ["customer-signal", provider, stage]
    metadata = {"provider": provider, "stage": stage}
    if context is not None:
        metadata.update(
            {
                "run_id": context.run_id,
                "run_kind": context.run_kind,
                "enabled_sources": ",".join(context.source_ids),
                "langfuse_session_id": context.run_id,
                "langfuse_tags": tags + [context.run_kind],
            }
        )
    handler = _new_callback_handler()
    return {
        "callbacks": [handler] if handler is not None else [],
        "run_name": run_name,
        "tags": tags,
        "metadata": metadata,
    }
```

`sanitize_trace_value`는 secret 계열 key, 이메일과 전화번호 패턴, user가 아닌 Agent
message content를 재귀적으로 마스킹합니다. `_new_callback_handler`는 세 필수 env가
없거나 SDK 초기화가 실패하면 `None`을 반환합니다. 실제 client는
`Langfuse(mask_otel_spans=...)`로 한 번만 생성합니다.

- [ ] **Step 5: Helper 테스트 통과 확인**

Run:

```bash
uv run --project backend pytest backend/tests/test_langfuse_observability.py -q
uv run --project backend ruff check backend/src/customer_signal/observability backend/tests/test_langfuse_observability.py
```

Expected: 전체 PASS

- [ ] **Step 6: 커밋**

```bash
git add backend/src/customer_signal/observability backend/tests/test_langfuse_observability.py
git commit -m "feat: (observability) Langfuse Run Context와 마스킹 추가"
```

### Task 3: 범용 Gemini 단계와 Primitive 관측

**Files:**
- Modify: `backend/tests/test_generic_gemini.py`
- Modify: `backend/src/customer_signal/agent/generic_gemini.py`
- Modify: `backend/tests/test_analysis_loop.py`
- Modify: `backend/src/customer_signal/agent/analysis_loop.py`

- [ ] **Step 1: 범용 callback config 실패 테스트 작성**

기존 `invoke_config` 기대값에 callback과 session metadata를 추가합니다. 테스트에서는
`_new_callback_handler`를 sentinel 객체로 바꿉니다.

```python
sentinel_handler = object()
monkeypatch.setattr(
    "customer_signal.observability.langfuse._new_callback_handler",
    lambda: sentinel_handler,
)

assert call["invoke_config"]["callbacks"] == [sentinel_handler]
assert call["invoke_config"]["run_name"] == f"customer_signal.{stage}"
assert call["invoke_config"]["metadata"]["stage"] == stage
```

- [ ] **Step 2: 범용 테스트 RED 확인**

Run:

```bash
uv run --project backend pytest backend/tests/test_generic_gemini.py -q
```

Expected: `callbacks` key 누락으로 FAIL

- [ ] **Step 3: 범용 Gemini 최소 구현**

`_invoke_model`의 수동 config 생성을 다음 호출로 교체한 뒤 기존 `schema_title`을
metadata에 추가합니다.

```python
config = build_langfuse_config(
    run_name=f"customer_signal.{stage}",
    provider="gemini",
    stage=stage,
)
config["metadata"]["schema_title"] = schema_title
```

- [ ] **Step 4: Primitive 공개 observation 실패 테스트 작성**

Analysis Loop 테스트의 fake observation recorder로 다음을 검증합니다.

```python
assert observation.name == "customer_signal.tool.match_sequence"
assert observation.input["primitive"] == "match_sequence"
assert observation.input["source_ids"] == ["search_history", "voc"]
assert observation.output["fact_id"] == fact.fact_id
assert observation.output["metrics"][0]["metric_key"] == "matched_customer_count"
```

- [ ] **Step 5: Primitive 테스트 RED 확인**

Run:

```bash
uv run --project backend pytest backend/tests/test_analysis_loop.py -q
```

Expected: Tool observation이 없어 FAIL

- [ ] **Step 6: Primitive 최소 구현**

`execute_async` 호출을 `public_observation` context manager로 감쌉니다.

```python
with public_observation(
    name=f"customer_signal.tool.{step.primitive}",
    stage="tool",
    input={
        "step_id": step.step_id,
        "primitive": step.primitive,
        "source_ids": list(step.source_ids),
        "parameters": step.parameters.model_dump(mode="json"),
    },
) as observation:
    fact = await self._executor.execute_async(
        step,
        scope=scope.model_copy(update={"source_ids": list(step.source_ids)}),
        prior_facts=facts,
        budget=budget,
    )
    observation.update(output=fact.model_dump(mode="json"))
```

Helper가 비활성 상태일 때는 no-op observation을 반환합니다.

- [ ] **Step 7: 범용 테스트 GREEN 확인과 커밋**

Run:

```bash
uv run --project backend pytest backend/tests/test_generic_gemini.py backend/tests/test_analysis_loop.py -q
```

Expected: 전체 PASS

```bash
git add backend/src/customer_signal/agent/generic_gemini.py backend/src/customer_signal/agent/analysis_loop.py backend/tests/test_generic_gemini.py backend/tests/test_analysis_loop.py
git commit -m "feat: (observability) 범용 Gemini와 Primitive Trace 연결"
```

### Task 4: DeepAgent callback과 Coordinator Run 연결

**Files:**
- Modify: `backend/tests/test_gemini_adapter.py`
- Modify: `backend/src/customer_signal/agent/gemini.py`
- Modify: `backend/src/customer_signal/runtime/coordinator.py`
- Modify: `backend/tests/test_runtime_generic.py`

- [ ] **Step 1: DeepAgent config 실패 테스트 작성**

테스트 `_ReplayAgent.ainvoke`가 config를 받아 기록하도록 바꿉니다.

```python
async def ainvoke(
    self,
    state: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    self.invoke_config = config
    ...
```

Run 이후 다음을 검증합니다.

```python
assert replay_agent.invoke_config["run_name"] == "customer_signal.agent"
assert replay_agent.invoke_config["callbacks"] == [sentinel_handler]
assert replay_agent.invoke_config["metadata"]["stage"] == "agent"
```

- [ ] **Step 2: DeepAgent RED 확인**

Run:

```bash
uv run --project backend pytest backend/tests/test_gemini_adapter.py -q
```

Expected: `_Agent.ainvoke`에 config가 전달되지 않아 FAIL

- [ ] **Step 3: DeepAgent 최소 구현**

Protocol과 호출을 다음 형태로 바꿉니다.

```python
class _Agent(Protocol):
    async def ainvoke(
        self,
        state: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


async def _invoke(self, model_name: str, state: dict[str, Any]) -> dict[str, Any]:
    agent = await self._get_agent(model_name)
    config = build_langfuse_config(
        run_name="customer_signal.agent",
        provider="gemini",
        stage="agent",
    )
    config["metadata"]["model"] = model_name
    result = await agent.ainvoke(state, config=config)
    ...
```

- [ ] **Step 4: Coordinator Run Context 실패 테스트 작성**

Generic과 Legacy runner fake가 현재 Context의 session을 기록하게 하고 다음을
검증합니다.

```python
assert captured_context.run_id == snapshot.run_id
assert captured_context.run_kind == "generic"
assert captured_context.question == request.question
assert captured_context.source_ids == tuple(request.enabled_sources)
```

- [ ] **Step 5: Coordinator RED 확인**

Run:

```bash
uv run --project backend pytest backend/tests/test_runtime_generic.py backend/tests/test_api.py -q
```

Expected: 활성 Run Context가 없어 FAIL

- [ ] **Step 6: Coordinator 최소 구현과 flush**

`_execute`와 `_execute_generic`에서 runner 또는 loop 호출만 다음 context로 감쌉니다.

```python
context = LangfuseRunContext(
    run_id=run_id,
    run_kind="generic",
    question=request.question,
    source_ids=tuple(request.enabled_sources),
)
with bind_langfuse_run(context):
    outcome = await loop.run(request, emit=emit)
```

Legacy는 `run_kind="legacy"`를 사용합니다. `close()` 마지막에는
`await asyncio.to_thread(flush_langfuse)`를 호출하고 Helper 내부에서 모든 오류를
흡수합니다.

- [ ] **Step 7: DeepAgent와 Coordinator GREEN 확인 및 커밋**

Run:

```bash
uv run --project backend pytest backend/tests/test_gemini_adapter.py backend/tests/test_runtime_generic.py backend/tests/test_api.py -q
```

Expected: 전체 PASS

```bash
git add backend/src/customer_signal/agent/gemini.py backend/src/customer_signal/runtime/coordinator.py backend/tests/test_gemini_adapter.py backend/tests/test_runtime_generic.py
git commit -m "feat: (observability) DeepAgent와 API Run Trace 연결"
```

### Task 5: 문서와 전체 검증

**Files:**
- Modify: `README.md`
- Modify: `docs/verification/live-gemini-smoke.md`

- [ ] **Step 1: README 업데이트**

다음 환경과 동작을 설명합니다.

```dotenv
LANGFUSE_SECRET_KEY=your_secret_key
LANGFUSE_PUBLIC_KEY=your_public_key
LANGFUSE_BASE_URL=http://localhost:3100
LANGFUSE_TRACING_ENVIRONMENT=development
```

`make dev-gemini`가 Backend에만 값을 전달하며, 같은 Run의 단계는 `run_id` session으로
묶이고 Langfuse 장애가 분석을 막지 않는다고 기록합니다.

- [ ] **Step 2: 전체 자동 검증**

Run:

```bash
uv run --project backend pytest backend/tests -q
uv run --project backend ruff check backend
```

Expected: 전체 PASS와 Ruff 오류 0개

- [ ] **Step 3: Gemini 서버 재시작**

실행 중인 서버를 `Ctrl-C`로 종료한 뒤 다음 명령을 실행합니다.

```bash
make BACKEND_PORT=38000 FRONTEND_PORT=33000 dev-gemini
```

Expected: Backend와 Frontend startup 완료

- [ ] **Step 4: 실제 합성 Run 실행**

질문 `반복 행동 뒤 상담으로 전환되는 Journey를 보여줘.`와 기본 기간, Source 5개로
`POST /api/runs?mode=gemini`를 실행합니다.

Expected:

```text
status=completed
agent_mode=gemini
error=null
```

- [ ] **Step 5: Langfuse 적재 확인**

SDK client의 `auth_check()`가 성공한 뒤 `flush()`를 실행합니다. 최신 Trace에서 다음을
확인합니다.

```text
session_id=<공개 Run ID>
run_name=customer_signal.goal 또는 customer_signal.agent
stage=goal, plan, note, selection, report, tool 중 실행 단계
input=마스킹된 사용자 발화 또는 공개 Tool parameters
output=구조화 Goal/Plan 또는 검증된 Fact/보고서
```

Key, 이메일 테스트 값, `private reasoning` 문자열이 검색되지 않아야 합니다.

- [ ] **Step 6: 문서와 최종 커밋**

```bash
git add README.md docs/verification/live-gemini-smoke.md
git commit -m "docs: (observability) Langfuse 실행과 검증 방법 추가"
```
