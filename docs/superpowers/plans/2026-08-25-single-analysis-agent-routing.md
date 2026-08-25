# Single Analysis Agent Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 모든 신규 API Run을 검증 가능한 Generic Analysis Loop 하나로 실행하고 기존 Journey 문구도 같은 Goal, Plan, Primitive, Fact, Note, Report 흐름으로 처리한다.

**Architecture:** FastAPI 진입점에서 질문 정규식 기반 Legacy 분기를 제거하고 `RunCoordinator.create_run(..., generic=True)`만 호출한다. 기존 Journey DeepAgent와 Legacy Artifact 타입은 삭제하지 않고 과거 기록 호환용으로 유지하며, Fixture와 Gemini는 실행 구조가 아닌 Provider 선택으로만 사용한다.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, pytest, Ruff, React 19, Playwright, Langfuse

---

## 파일 구조

| 파일 | 책임 |
| --- | --- |
| `backend/src/customer_signal/agent/generic_fixture.py` | 기존 Journey 데모 문구를 범용 repeat Journey 목표로 구조화 |
| `backend/src/customer_signal/api.py` | 모든 신규 Run을 Generic Analysis Loop로 전달 |
| `backend/tests/test_analysis_loop.py` | 기존 Journey 문구의 범용 Goal·Plan·Fact 회귀 검증 |
| `backend/tests/test_api.py` | 질문과 Provider mode에 무관한 단일 라우팅 계약 검증 |
| `frontend/e2e/working-demo.spec.ts` | 기존 Journey 문구가 단일 Analysis Workspace에서 완료되는 브라우저 검증 |
| `README.md` | 단일 Analysis Agent 구조와 Langfuse 단계 설명 |
| `docs/team-demo-guide.md` | 팀 시연 문구에서 Generic/DeepAgent 이중 경로 제거 |
| `Makefile` | 기존 Journey E2E 명령을 호환 시나리오로 설명 |

### Task 1: 기존 Journey 문구를 범용 목표로 수용

**Files:**
- Modify: `backend/tests/test_analysis_loop.py:450-490`
- Modify: `backend/src/customer_signal/agent/generic_fixture.py:386-411`

- [ ] **Step 1: 기존 Journey 문구의 실패 테스트 작성**

`test_loop_executes_three_distinct_fact_backed_demo_questions`를
`test_loop_executes_supported_fact_backed_demo_questions`로 바꾸고 다음 case를 추가한다.

```python
(
    "AI 검색 실패 후 고객센터까지 문의한 고객이 얼마나 돼?",
    "match_sequence",
    "matched_customer_count",
    6,
),
```

- [ ] **Step 2: 테스트가 지원 범위 오류로 실패하는지 확인**

Run:

```bash
uv run --project backend pytest \
  backend/tests/test_analysis_loop.py::test_loop_executes_supported_fact_backed_demo_questions \
  -q
```

Expected: 새 Journey case가 `outcome.status == "completed"`에서 실패하고 실제 상태는
`failed`다.

- [ ] **Step 3: 기존의 엄격한 Journey intent를 repeat 시나리오에 연결**

`generic_fixture.py`에 기존 intent 판별기를 import한다.

```python
from customer_signal.agent.intent import is_supported_target_journey_question
```

`_scenario()`의 안전하지 않은 요청 판별 뒤, 일반 키워드 판별 전에 다음 분기를 추가한다.

```python
if is_supported_target_journey_question(question):
    return "repeat"
```

이 순서는 PII·원본·쓰기 요청이 Journey 문구를 포함하더라도 먼저 거부되게 유지한다.

- [ ] **Step 4: 범용 Analysis Loop 테스트 통과 확인**

Run:

```bash
uv run --project backend pytest \
  backend/tests/test_analysis_loop.py::test_loop_executes_supported_fact_backed_demo_questions \
  -q
```

Expected: `4 passed`.

- [ ] **Step 5: Journey 문구 호환 변경 커밋**

```bash
git add backend/src/customer_signal/agent/generic_fixture.py \
  backend/tests/test_analysis_loop.py
git commit -m "feat: (agent) 기존 Journey 문구를 범용 목표로 수용"
```

### Task 2: API를 Generic 단일 경로로 고정

**Files:**
- Modify: `backend/tests/test_api.py:214-239,516-538`
- Modify: `backend/src/customer_signal/api.py:18-22,300-319,432-433`

- [ ] **Step 1: 질문과 Provider mode를 함께 기록하는 라우팅 Probe 작성**

`RoutingProbeCoordinator`의 기록 값을 다음처럼 바꾼다.

```python
class RoutingProbeCoordinator:
    def __init__(self) -> None:
        self.calls: list[tuple[bool, str | None]] = []

    def create_run(self, request, *, generic=False, mode=None):
        del request
        self.calls.append((generic, mode))

        class Snapshot:
            run_id = "routing-probe-run"

        return Snapshot()
```

- [ ] **Step 2: 기존 Journey 질문도 Generic이고 mode는 Provider만 바꾸는 테스트 작성**

기존 두 라우팅 테스트를 다음 계약으로 교체한다.

```python
@pytest.mark.parametrize(
    ("question", "query", "expected_mode"),
    [
        ("AI 검색 실패 후 고객센터까지 문의한 고객이 얼마나 돼?", "", "fixture"),
        ("최근 이탈 고객의 공통 행동 경로를 Source별로 비교해줘.", "", "fixture"),
        ("AI 검색 실패 후 고객센터까지 문의한 고객이 얼마나 돼?", "?mode=gemini", "gemini"),
    ],
)
def test_all_questions_route_to_generic_loop(
    tmp_path: Path,
    question: str,
    query: str,
    expected_mode: str,
) -> None:
    app, coordinator = _create_routing_probe_app(tmp_path / "routing.duckdb")

    with TestClient(app) as client:
        response = client.post(f"/api/runs{query}", json=_run_request(question=question))

    assert response.status_code == 202
    assert coordinator.calls == [(True, expected_mode)]
```

- [ ] **Step 3: 현재 Legacy 분기로 인해 RED인지 확인**

Run:

```bash
uv run --project backend pytest \
  backend/tests/test_api.py::test_all_questions_route_to_generic_loop \
  -q
```

Expected: mode가 없는 기존 Journey case에서 `(False, "fixture")`가 기록되어 1개 case가
실패한다.

- [ ] **Step 4: FastAPI 진입점을 단일화**

`api.py`에서 `is_supported_target_journey_question` import와
`_is_generic_question()`을 삭제한다. `create_run()`의 분기를 다음으로 교체한다.

```python
selected_mode = mode or resolved.generic_default_mode
snapshot = resolved.coordinator.create_run(
    request,
    generic=True,
    mode=selected_mode,
)
```

- [ ] **Step 5: 라우팅 테스트와 API 회귀 테스트 실행**

Run:

```bash
uv run --project backend pytest \
  backend/tests/test_api.py::test_all_questions_route_to_generic_loop \
  backend/tests/test_api.py -q
```

Expected: 라우팅 parametrization은 모두 통과한다. 기존 Legacy SSE 형태를 직접 기대하던
API 테스트가 실패하면 Task 3에서 공개 Generic lifecycle 기대값으로 전환한다.

- [ ] **Step 6: 단일 라우팅 변경 커밋**

```bash
git add backend/src/customer_signal/api.py backend/tests/test_api.py
git commit -m "feat: (agent) 모든 질문을 범용 분석 경로로 통일"
```

### Task 3: 공개 데모와 문서를 단일 Agent 계약으로 정리

**Files:**
- Modify: `backend/tests/test_api.py:400-460`
- Modify: `frontend/e2e/working-demo.spec.ts`
- Modify: `README.md:45-63,142-164,170-180`
- Modify: `docs/team-demo-guide.md:198-220`
- Modify: `Makefile:20-26,140-141`

- [ ] **Step 1: API 완료 Run이 Generic lifecycle을 공개하는 회귀 assertion 작성**

`test_run_completes_with_public_snapshot_and_contiguous_sse`에 다음 핵심 계약을 둔다.

```python
assert snapshot["status"] == "completed"
assert snapshot["run_kind"] == "generic"
assert snapshot["agent_mode"] == "fixture"
assert snapshot["report"]["report_kind"] == "customer_signal"
event_types = [event["event"] for event in events]
assert event_types[:3] == ["run_started", "goal_created", "plan_created"]
assert "step_started" in event_types
assert "fact_created" in event_types
assert "analysis_note_created" in event_types
assert event_types[-3:] == ["report_validating", "result", "done"]
```

Legacy `plan`, `tool_started`, `tool_completed`, `validating`의 정확한 배열을 기대하는
assertion과 `TOOL_NAMES` 상수는 제거한다.

- [ ] **Step 2: API 완료 Run 테스트 통과 확인**

Run:

```bash
uv run --project backend pytest \
  backend/tests/test_api.py::test_run_completes_with_public_snapshot_and_contiguous_sse \
  -q
```

Expected: `1 passed`이며 기존 Journey 문구의 신규 snapshot은 `run_kind="generic"`이다.

- [ ] **Step 3: Journey 브라우저 시나리오를 Analysis Workspace 기준으로 전환**

`working-demo.spec.ts`에서 Legacy 전용 metric label과 자동 Journey 상세 assertion을
다음 범용 결과 assertion으로 교체한다.

```typescript
const workspace = page.getByRole("region", { name: "분석 Workspace" });
await expect(
  workspace.getByRole("heading", {
    name: "반복 행동 뒤 상담으로 이어진 고객 Journey를 확인합니다.",
  }),
).toBeVisible();
await expect(
  workspace.getByRole("heading", { name: "match_sequence", exact: true }).first(),
).toBeVisible();
await expect(
  workspace.getByText("Matched Customer Count", { exact: true }).first(),
).toBeVisible();
await expect(
  workspace.getByText("6customers", { exact: true }).first(),
).toBeVisible();
```

두 번째 VOC 제외 case는 `Matched Customer Count`의 `0customers`와 no-data limitation을
기준으로 검증한다. 상세 Journey와 Evidence API 자체의 Legacy Artifact 호환 테스트는
기존 Backend·Frontend 단위 테스트에 남긴다.

- [ ] **Step 4: README와 팀 데모 가이드에서 이중 런타임 설명 제거**

README 구조도에서 `Legacy Journey Runner` 가지를 삭제하고 Langfuse 관측 표에서
`customer_signal.agent` 행을 제거한다. 팀 데모 가이드에는 모든 질문이 다음 동일한
Trace를 사용한다고 명시한다.

```text
customer_signal.turn
  → customer_signal.goal
  → customer_signal.plan
  → customer_signal.tool.<primitive>
  → customer_signal.note / customer_signal.selection
  → customer_signal.report
```

`make e2e-legacy`의 설명은 삭제하지 않고 기존 자동화 호출 호환을 위해
`기존 Journey 문구의 단일 Analysis Agent 호환 E2E`로 바꾼다.

- [ ] **Step 5: 문서와 데모 변경 검증**

Run:

```bash
git diff --check
uv run --project backend pytest backend/tests/test_api.py -q
npm --prefix frontend run e2e -- working-demo.spec.ts
```

Expected: whitespace 오류 없음, API 테스트 전체 통과, Journey 데모의 desktop/mobile
프로젝트 통과.

- [ ] **Step 6: 데모 계약 정리 커밋**

```bash
git add backend/tests/test_api.py frontend/e2e/working-demo.spec.ts \
  README.md docs/team-demo-guide.md Makefile
git commit -m "docs: (demo) 단일 Analysis Agent 시연 흐름 정리"
```

### Task 4: 전체 검증과 실제 Gemini Trace 확인

**Files:**
- Modify: `docs/verification/live-gemini-smoke.md`

- [ ] **Step 1: Backend 전체 회귀와 정적 검사 실행**

Run:

```bash
uv run --project backend pytest backend/tests -q
uv run --project backend ruff check backend
```

Expected: pytest 실패 0개, Ruff 오류 0개. Legacy Artifact 조회·렌더링 테스트도 유지된다.

- [ ] **Step 2: Frontend 단위·타입·빌드 검증**

Run:

```bash
npm --prefix frontend test -- --run
npm --prefix frontend run typecheck
NEXT_PUBLIC_API_BASE_URL="http://127.0.0.1:38000" npm --prefix frontend run build
```

Expected: Vitest 실패 0개, TypeScript 오류 0개, Next.js production build 성공.

- [ ] **Step 3: 깨끗한 Gemini Backend에서 LangSmith 초기화 확인**

선택한 Backend 환경 파일을 `backend/.env`로 두고 shell 상속값을 제거한 새 subprocess에서
다음을 실행한다.

```bash
env -u LANGSMITH_TRACING -u LANGSMITH_API_KEY -u LANGSMITH_PROJECT \
  -u LANGSMITH_ENDPOINT -u LANGCHAIN_TRACING_V2 -u LANGCHAIN_API_KEY \
  uv run --env-file backend/.env --project backend python -c \
  'import langsmith.utils; assert langsmith.utils.tracing_is_enabled()'
```

Expected: exit code 0. 환경 파일이나 Key 원문은 출력하지 않는다.

- [ ] **Step 4: 서버 재시작 후 기존 Journey 문구를 실제 Gemini로 한 번 실행**

Backend는 LangSmith 환경이 import 전에 설정되도록 기존 Gemini launcher로 다시 시작한다.

```bash
make dev-gemini BACKEND_PORT=38000 FRONTEND_PORT=33000
```

브라우저에서 다음 질문을 한 번 실행한다.

```text
AI 검색 실패 후 고객센터까지 문의한 고객이 얼마나 돼?
```

Expected: Run이 `completed`, snapshot이 `run_kind="generic"`, `agent_mode="gemini"`이고
화면에 Goal, Plan, Tool, Fact, Note, Report가 표시된다.

- [ ] **Step 5: Langfuse 단일 Turn Trace 검증 기록 갱신**

공개 `run_id`로 Langfuse Session을 조회해 다음을 확인하고
`docs/verification/live-gemini-smoke.md`에 Key나 Provider 원문 없이 기록한다.

```text
Trace name: customer_signal.turn
run_kind: generic
children: goal, plan, tool.*, note, selection, report
customer_signal.agent child: absent
```

- [ ] **Step 6: 최종 검증 기록 커밋**

```bash
git add docs/verification/live-gemini-smoke.md
git commit -m "docs: (verification) 단일 Analysis Agent 실호출 검증"
```
