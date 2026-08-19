# Customer Journey Agent MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사용자가 한국어 질문을 입력하고, 실제 DuckDB 분석과 MCP Tool 실행 Trace, Insight, 고객 Journey, Evidence를 브라우저에서 상호작용하는 로컬 MVP를 구현합니다.

**Architecture:** Backend의 순수 Analysis Service가 DuckDB에서 수치, 고객 매칭, Risk Score와 Evidence를 계산합니다.
FastMCP와 fixture runner가 같은 Service를 사용하고 FastAPI가 Run과 SSE contract를 제공합니다.
Next.js 단일 페이지는 공개 SSE 이벤트와 상세 API만 소비합니다.
Gemini와 DeepAgents는 API key가 있을 때 같은 contract 뒤에서 선택 실행합니다.

**Tech Stack:** Python 3.12, Pydantic 2, DuckDB, FastMCP, FastAPI, DeepAgents, Gemini, pytest, Next.js 16, React 19, TypeScript, Vitest, Testing Library, Playwright

---

## 파일 구조

```text
backend/
├── pyproject.toml
├── .env.example
├── src/customer_signal/
│   ├── config.py
│   ├── domain/
│   │   ├── models.py
│   │   └── reports.py
│   ├── synthetic/
│   │   ├── generator.py
│   │   └── cli.py
│   ├── data/
│   │   ├── database.py
│   │   └── repository.py
│   ├── analytics/
│   │   ├── policies.py
│   │   └── service.py
│   ├── mcp_server.py
│   ├── agent/
│   │   ├── contracts.py
│   │   ├── fixture.py
│   │   ├── gemini.py
│   │   └── validator.py
│   ├── runtime/
│   │   ├── events.py
│   │   ├── run_store.py
│   │   └── coordinator.py
│   └── api.py
└── tests/
    ├── conftest.py
    ├── test_generator.py
    ├── test_analytics.py
    ├── test_mcp_server.py
    ├── test_fixture_runner.py
    ├── test_validator.py
    └── test_api.py
frontend/
├── package.json
├── package-lock.json
├── tsconfig.json
├── next.config.ts
├── vitest.config.ts
├── playwright.config.ts
├── src/
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── page.tsx
│   │   └── globals.css
│   └── features/customer-intelligence/
│       ├── contracts.ts
│       ├── parse-sse.ts
│       ├── run-reducer.ts
│       ├── run-client.ts
│       ├── use-run-controller.ts
│       ├── CustomerIntelligencePage.tsx
│       ├── QueryComposer.tsx
│       ├── AgentTrace.tsx
│       ├── InsightSummary.tsx
│       ├── RankedCustomers.tsx
│       ├── JourneyTimeline.tsx
│       ├── EvidenceDrawer.tsx
│       └── __tests__/
│           ├── parse-sse.test.ts
│           ├── run-reducer.test.ts
│           └── CustomerIntelligencePage.test.tsx
└── e2e/working-demo.spec.ts
README.md
Makefile
```

`domain`은 저장소와 Framework를 모릅니다.
`analytics`는 DuckDB Repository contract만 사용합니다.
`mcp_server`, `agent`, `runtime`, `api`는 바깥쪽 Adapter입니다.
Frontend 컴포넌트는 네트워크를 직접 호출하지 않고 `use-run-controller.ts`가 상태 전이를 소유합니다.

## Task 1: Backend 실행 환경과 설정

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/.env.example`
- Create: `backend/src/customer_signal/__init__.py`
- Create: `backend/src/customer_signal/config.py`
- Create: `backend/tests/test_config.py`

- [ ] **Step 1: 설정 기본값을 고정하는 실패 테스트 작성**

```python
from customer_signal.config import Settings


def test_settings_default_to_fixture_without_api_key(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_MODE", raising=False)

    settings = Settings()

    assert settings.resolved_agent_mode == "fixture"
    assert settings.gemini_model == "gemini-3.6-flash"
    assert settings.api_port == 8000
```

- [ ] **Step 2: 테스트가 예상한 Import 오류로 실패하는지 확인**

Run: `uv run --project backend pytest backend/tests/test_config.py -q`

Expected: `ModuleNotFoundError: No module named 'customer_signal'`

- [ ] **Step 3: Python 3.12와 의존성 고정**

`backend/pyproject.toml`에 다음 project contract를 작성합니다.

```toml
[project]
name = "customer-signal-demo"
version = "0.1.0"
requires-python = "==3.12.*"
dependencies = [
  "deepagents==0.7.7",
  "duckdb==1.5.5",
  "fastapi==0.140.8",
  "fastmcp==3.4.7",
  "langchain==1.3.15",
  "langchain-google-genai==4.3.4",
  "langchain-mcp-adapters==0.3.2",
  "mcp==1.29.0",
  "pydantic-settings==2.13.1",
  "uvicorn==0.52.4",
]

[dependency-groups]
dev = [
  "httpx==0.28.1",
  "pytest==9.0.2",
  "pytest-asyncio==1.3.0",
  "ruff==0.15.5",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/customer_signal"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
asyncio_mode = "auto"

[tool.ruff]
line-length = 100
target-version = "py312"
```

- [ ] **Step 4: 최소 Settings 구현**

```python
from pathlib import Path
from typing import Literal

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    agent_mode: Literal["auto", "fixture", "gemini"] = "auto"
    google_api_key: str | None = None
    gemini_model: str = "gemini-3.6-flash"
    database_path: Path = Path("data/generated/customer_signal.duckdb")
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    frontend_origin: str = "http://127.0.0.1:3000"

    @computed_field
    @property
    def resolved_agent_mode(self) -> Literal["fixture", "gemini"]:
        if self.agent_mode == "fixture":
            return "fixture"
        if self.agent_mode == "gemini":
            return "gemini"
        return "gemini" if self.google_api_key else "fixture"
```

- [ ] **Step 5: 환경 설치와 테스트 통과 확인**

Run: `uv sync --project backend --python 3.12 && uv run --project backend pytest backend/tests/test_config.py -q`

Expected: `1 passed`

- [ ] **Step 6: 환경 Bootstrap 커밋**

```bash
git add backend/pyproject.toml backend/uv.lock backend/.env.example backend/src backend/tests/test_config.py
git commit -m "chore: (backend) 실행 환경과 설정 추가"
```

## Task 2: Domain contract와 합성 데이터

**Files:**
- Create: `backend/src/customer_signal/domain/models.py`
- Create: `backend/src/customer_signal/domain/reports.py`
- Create: `backend/src/customer_signal/synthetic/generator.py`
- Create: `backend/src/customer_signal/synthetic/cli.py`
- Create: `backend/tests/test_generator.py`

- [ ] **Step 1: 재현성과 정답 cohort 실패 테스트 작성**

```python
from customer_signal.synthetic.generator import generate_dataset


def test_dataset_is_seeded_and_contains_six_positive_customers():
    first = generate_dataset(seed=20260819)
    second = generate_dataset(seed=20260819)

    assert first.model_dump() == second.model_dump()
    assert len(first.customers) == 30
    assert len(first.ground_truth_customer_ids) == 6
    assert set(first.ground_truth_customer_ids) == {
        "CUST-003", "CUST-007", "CUST-011",
        "CUST-016", "CUST-022", "CUST-028",
    }
    assert {event.source_id for event in first.events} == {
        "search_history", "search_feedback", "voc",
    }
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run --project backend pytest backend/tests/test_generator.py -q`

Expected: generator module Import 오류

- [ ] **Step 3: Pydantic Domain 모델 구현**

`CustomerEvent`, `EvidenceRecord`, `SyntheticDataset`, `JourneyEvent`, `RankedCustomer`, `InsightReport`를 작성합니다.
모든 timestamp는 timezone이 있는 `datetime`으로 제한합니다.
`attributes` 값은 `str | int | float | bool | None`만 허용합니다.

```python
class CustomerEvent(BaseModel):
    event_id: str
    evidence_id: str
    source_id: Literal["search_history", "search_feedback", "voc"]
    occurred_at: datetime
    event_type: Literal["search", "feedback", "voc"]
    action: str
    topic: str
    outcome: str
    text: str
    canonical_customer_id: str
    attributes: dict[str, Scalar] = Field(default_factory=dict)


class SyntheticDataset(BaseModel):
    customers: list[str]
    events: list[CustomerEvent]
    evidence: list[EvidenceRecord]
    ground_truth_customer_ids: list[str]
```

- [ ] **Step 4: Scenario-first generator 구현**

고정 정답 고객에는 `failed search`, 24시간 내 같은 Topic `repeat search`, 선택적 `negative feedback`, 첫 실패 72시간 내 `unresolved VOC`를 생성합니다.
나머지 24명은 검색 성공, 다른 Topic 재검색, 72시간 이후 VOC, VOC 없는 검색 실패 중 하나로 생성합니다.
이벤트 ID는 seed와 고객 순번으로 만들고 랜덤 UUID를 사용하지 않습니다.

- [ ] **Step 5: 전체 generator 테스트 통과 확인**

Run: `uv run --project backend pytest backend/tests/test_generator.py -q`

Expected: generator 테스트 전체 통과

- [ ] **Step 6: Domain과 합성 데이터 커밋**

```bash
git add backend/src/customer_signal/domain backend/src/customer_signal/synthetic backend/tests/test_generator.py
git commit -m "feat: (data) 고객 Journey 합성 데이터 생성"
```

## Task 3: DuckDB Repository와 결정론적 분석

**Files:**
- Create: `backend/src/customer_signal/data/database.py`
- Create: `backend/src/customer_signal/data/repository.py`
- Create: `backend/src/customer_signal/analytics/policies.py`
- Create: `backend/src/customer_signal/analytics/service.py`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_analytics.py`

- [ ] **Step 1: 패턴 매칭과 Source ablation 실패 테스트 작성**

```python
def test_match_pattern_returns_ground_truth(analytics_service):
    result = analytics_service.match_journey_pattern(
        start_at="2026-07-20T00:00:00+09:00",
        end_at="2026-08-19T00:00:00+09:00",
        enabled_sources=["search_history", "search_feedback", "voc"],
    )

    assert result.customer_count == 6
    assert result.customer_ids == [
        "CUST-003", "CUST-007", "CUST-011",
        "CUST-016", "CUST-022", "CUST-028",
    ]
    assert all(customer.risk_score >= 75 for customer in result.customers)


def test_disabling_voc_removes_complete_pattern_matches(analytics_service):
    result = analytics_service.match_journey_pattern(
        start_at="2026-07-20T00:00:00+09:00",
        end_at="2026-08-19T00:00:00+09:00",
        enabled_sources=["search_history", "search_feedback"],
    )

    assert result.customer_count == 0
    assert result.candidate_count >= 6
    assert "voc" in result.missing_sources
```

- [ ] **Step 2: 실패 확인**

Run: `uv run --project backend pytest backend/tests/test_analytics.py -q`

Expected: database 또는 analytics module Import 오류

- [ ] **Step 3: DuckDB schema와 seed 구현**

`events`, `evidence`, `ground_truth` 테이블을 만듭니다.
CLI는 임시 파일에 DB를 만든 뒤 최종 경로로 원자적으로 교체합니다.
Runtime Repository는 Tool 호출마다 `read_only=True` connection을 열고 닫습니다.

```sql
CREATE TABLE events (
  event_id VARCHAR PRIMARY KEY,
  evidence_id VARCHAR NOT NULL,
  source_id VARCHAR NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL,
  event_type VARCHAR NOT NULL,
  action VARCHAR NOT NULL,
  topic VARCHAR NOT NULL,
  outcome VARCHAR NOT NULL,
  text VARCHAR NOT NULL,
  canonical_customer_id VARCHAR NOT NULL,
  attributes JSON NOT NULL
);
```

- [ ] **Step 4: Risk policy와 Analysis Service 구현**

정책은 검색 실패 25점, 24시간 내 같은 Topic 재검색 25점, 부정 피드백 20점, 72시간 내 같은 Topic VOC 30점입니다.
`catalog_sources`, `aggregate_events`, `match_journey_pattern`, `rank_customers`, `get_customer_journey`, `get_evidence`를 구현합니다.
모든 query는 allowlist Source, 시간 범위, 최대 100개 결과를 검증합니다.

- [ ] **Step 5: Ground Truth와 Evidence 검증**

Run: `uv run --project backend pytest backend/tests/test_analytics.py -q`

Expected: 전체 Source 6명, VOC 제외 0명, Journey 시간순, Evidence 마스킹 테스트 통과

- [ ] **Step 6: 데이터 계층 커밋**

```bash
git add backend/src/customer_signal/data backend/src/customer_signal/analytics backend/tests
git commit -m "feat: (analytics) DuckDB 고객 패턴 분석 추가"
```

## Task 4: Read-only FastMCP Tool 경계

**Files:**
- Create: `backend/src/customer_signal/mcp_server.py`
- Create: `backend/tests/test_mcp_server.py`

- [ ] **Step 1: 실제 MCP client 실패 테스트 작성**

```python
from fastmcp import Client

from customer_signal.mcp_server import create_mcp_server


async def test_mcp_match_tool_returns_six_customers(analytics_service):
    server = create_mcp_server(analytics_service)

    async with Client(server) as client:
        response = await client.call_tool("match_journey_pattern", {
            "start_at": "2026-07-20T00:00:00+09:00",
            "end_at": "2026-08-19T00:00:00+09:00",
            "enabled_sources": ["search_history", "search_feedback", "voc"],
        })

    assert response.data["customer_count"] == 6
```

- [ ] **Step 2: 실패 확인**

Run: `uv run --project backend pytest backend/tests/test_mcp_server.py -q`

Expected: `create_mcp_server` Import 오류

- [ ] **Step 3: 6개 Tool을 얇은 Adapter로 등록**

```python
from fastmcp import FastMCP


def create_mcp_server(service: AnalyticsService) -> FastMCP:
    mcp = FastMCP("Customer Signal Data")

    @mcp.tool
    def catalog_sources(start_at: str, end_at: str) -> dict:
        return service.catalog_sources(start_at, end_at).model_dump(mode="json")

    @mcp.tool
    def match_journey_pattern(
        start_at: str,
        end_at: str,
        enabled_sources: list[str],
    ) -> dict:
        return service.match_journey_pattern(
            start_at=start_at,
            end_at=end_at,
            enabled_sources=enabled_sources,
        ).model_dump(mode="json")

    return mcp
```

나머지 4개 Tool도 같은 방식으로 등록합니다. Tool docstring에는 허용 입력, 최대 반환 수, Ground Truth 접근 금지를 적습니다.

- [ ] **Step 4: Tool schema와 호출 테스트 통과 확인**

Run: `uv run --project backend pytest backend/tests/test_mcp_server.py -q`

Expected: Tool 목록 6개, protocol 호출, 잘못된 Source 거부 테스트 통과

- [ ] **Step 5: MCP 커밋**

```bash
git add backend/src/customer_signal/mcp_server.py backend/tests/test_mcp_server.py
git commit -m "feat: (mcp) 읽기 전용 고객 분석 도구 추가"
```

## Task 5: Fixture Runner와 의미 검증

**Files:**
- Create: `backend/src/customer_signal/agent/contracts.py`
- Create: `backend/src/customer_signal/agent/fixture.py`
- Create: `backend/src/customer_signal/agent/validator.py`
- Create: `backend/src/customer_signal/runtime/events.py`
- Create: `backend/tests/test_fixture_runner.py`
- Create: `backend/tests/test_validator.py`

- [ ] **Step 1: Runner event 순서와 결과 실패 테스트 작성**

```python
async def test_fixture_runner_emits_trace_and_report(fixture_runner):
    events = []

    report = await fixture_runner.run(
        question="AI검색에서 해결 못 하고 고객센터에 문의한 고객이 몇 명이야?",
        start_at="2026-07-20T00:00:00+09:00",
        end_at="2026-08-19T00:00:00+09:00",
        enabled_sources=["search_history", "search_feedback", "voc"],
        emit=events.append,
    )

    assert report.metrics[0].value == 6
    assert [event.type for event in events] == [
        "plan", "tool_started", "tool_completed",
        "tool_started", "tool_completed",
        "tool_started", "tool_completed",
        "tool_started", "tool_completed", "validating", "result",
    ]
```

- [ ] **Step 2: 의미 검증 실패 테스트 작성**

```python
def test_validator_rejects_unknown_evidence(valid_report, run_facts):
    invalid = valid_report.model_copy(deep=True)
    invalid.findings[0].evidence_ids = ["EVIDENCE-NOT-IN-RUN"]

    with pytest.raises(UnsupportedClaimError):
        validate_report(invalid, run_facts)
```

- [ ] **Step 3: 지원 질문 판별과 Fixture Runner 구현**

Runner는 `catalog_sources → aggregate_events → match_journey_pattern → rank_customers` 순서로 같은 Analysis Service를 호출합니다.
질문에 `검색`, `고객센터`, `상담`, `문의`, `재검색` 중 하나도 없으면 `UnsupportedQuestionError`를 반환합니다.
Report의 headline, Scope, Action 문구는 계산된 cohort와 missing Source에 따라 조립합니다.

- [ ] **Step 4: Validator 구현**

Validator는 Report의 고객 ID, metric 값, Evidence ID가 해당 Run의 Tool 결과에 존재하는지 확인합니다. 검증 실패 시 결과를 공개하지 않고 `error` event를 만듭니다.

- [ ] **Step 5: Runner와 Validator 테스트 통과 확인**

Run: `uv run --project backend pytest backend/tests/test_fixture_runner.py backend/tests/test_validator.py -q`

Expected: event 순서, 지원하지 않는 질문, 0명, 위조 ID 거부 테스트 통과

- [ ] **Step 6: Runner 커밋**

```bash
git add backend/src/customer_signal/agent backend/src/customer_signal/runtime/events.py backend/tests
git commit -m "feat: (agent) 근거 검증형 fixture 분석 실행 추가"
```

## Task 6: FastAPI Run API와 재연결 가능한 SSE

**Files:**
- Create: `backend/src/customer_signal/runtime/run_store.py`
- Create: `backend/src/customer_signal/runtime/coordinator.py`
- Create: `backend/src/customer_signal/api.py`
- Create: `backend/tests/test_api.py`

- [ ] **Step 1: Run 생성과 SSE replay 실패 테스트 작성**

```python
async def test_run_api_streams_result_and_replays_after_event_id(api_client):
    created = await api_client.post("/api/runs", json={
        "question": "검색 실패 후 고객센터까지 문의한 고객은?",
        "start_at": "2026-07-20T00:00:00+09:00",
        "end_at": "2026-08-19T00:00:00+09:00",
        "enabled_sources": ["search_history", "search_feedback", "voc"],
    })
    assert created.status_code == 202
    run_id = created.json()["run_id"]

    snapshot = await wait_for_completed(api_client, run_id)
    assert snapshot["report"]["metrics"][0]["value"] == 6

    replay = await api_client.get(
        f"/api/runs/{run_id}/events",
        headers={"Last-Event-ID": "2"},
    )
    assert "id: 3" in replay.text
    assert "event: result" in replay.text
```

- [ ] **Step 2: 실패 확인**

Run: `uv run --project backend pytest backend/tests/test_api.py -q`

Expected: API module Import 오류

- [ ] **Step 3: 메모리 RunStore와 Coordinator 구현**

RunStore는 `asyncio.Condition`으로 새 이벤트를 알리고 event ID를 1부터 증가시킵니다.
Coordinator는 Background Task에서 Runner를 실행합니다.
완료, 실패, fallback 상태를 snapshot에 저장하고 같은 Run의 Journey와 Evidence allowlist를 유지합니다.

- [ ] **Step 4: FastAPI와 FastMCP mount 구현**

```python
def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="Customer Signal Agent")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.mount("/mcp", mcp.http_app(path="/"))
    return app
```

`POST /api/runs`, `GET /api/runs/{id}`, `GET /api/runs/{id}/events`, Journey, Evidence, `/health`를 추가합니다.
SSE는 event ID, event type, JSON data를 보내고 `Last-Event-ID` 이후부터 replay합니다.
FastAPI lifespan 안에서 FastMCP ASGI lifespan도 함께 실행합니다.

- [ ] **Step 5: API와 전체 Backend 테스트 통과 확인**

Run: `uv run --project backend pytest backend/tests -q && uv run --project backend ruff check backend/src backend/tests`

Expected: 전체 테스트와 Ruff 통과

- [ ] **Step 6: API 커밋**

```bash
git add backend/src/customer_signal/runtime backend/src/customer_signal/api.py backend/tests/test_api.py
git commit -m "feat: (api) 분석 Run과 SSE 스트리밍 추가"
```

## Task 7: Frontend contract, SSE parser와 상태 Reducer

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/package-lock.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/next.config.ts`
- Create: `frontend/vitest.config.ts`
- Create: `frontend/src/test/setup.ts`
- Create: `frontend/src/features/customer-intelligence/contracts.ts`
- Create: `frontend/src/features/customer-intelligence/parse-sse.ts`
- Create: `frontend/src/features/customer-intelligence/run-reducer.ts`
- Create: `frontend/src/features/customer-intelligence/run-client.ts`
- Create: `frontend/src/features/customer-intelligence/__tests__/parse-sse.test.ts`
- Create: `frontend/src/features/customer-intelligence/__tests__/run-reducer.test.ts`

- [ ] **Step 1: 잘린 SSE chunk 실패 테스트 작성**

```typescript
it("joins JSON split across network chunks", () => {
  const parser = createSseParser();

  expect(parser.push('id: 1\nevent: tool_completed\ndata: {"tool":"match_')).toEqual([]);
  expect(parser.push('journey_pattern","count":6}\n\n')).toEqual([
    {
      id: 1,
      type: "tool_completed",
      data: { tool: "match_journey_pattern", count: 6 },
    },
  ]);
});
```

- [ ] **Step 2: Run ID 격리 실패 테스트 작성**

```typescript
it("ignores late events from the previous run", () => {
  const state = { ...initialRunState, runId: "run-new", phase: "running" };
  const next = runReducer(state, {
    kind: "event",
    runId: "run-old",
    event: { id: 9, type: "result", data: completedReport },
  });

  expect(next).toBe(state);
});
```

- [ ] **Step 3: Next.js와 테스트 도구 설치**

Run:

```bash
npm install --prefix frontend next@16.3.1 react@19.2.8 react-dom@19.2.8
npm install --prefix frontend --save-dev typescript vitest jsdom @vitejs/plugin-react \
  @testing-library/react @testing-library/jest-dom @testing-library/user-event \
  @types/node @types/react @types/react-dom
```

Expected: `frontend/package-lock.json` 생성과 dependency 설치 성공

- [ ] **Step 4: 공개 contract와 순수 Parser/Reducer 구현**

상태는 `idle | running | validating | completed | degraded | failed`로 제한합니다.
Parser는 `id`, `event`, 여러 `data` 줄, chunk remainder를 처리합니다.
Reducer는 현재 `runId` 이벤트만 누적하고 `result` 시 ranked customer 첫 항목을 선택합니다.

- [ ] **Step 5: Frontend 단위 테스트 통과 확인**

Run: `npm --prefix frontend test -- --run`

Expected: parser와 reducer 테스트 통과

- [ ] **Step 6: Frontend 기반 커밋**

```bash
git add frontend/package.json frontend/package-lock.json frontend/tsconfig.json frontend/next.config.ts frontend/vitest.config.ts frontend/src
git commit -m "feat: (frontend) 분석 스트림 상태 관리 추가"
```

## Task 8: 질문, Trace, Insight, Journey와 Evidence UI

**Files:**
- Create: `frontend/src/app/layout.tsx`
- Create: `frontend/src/app/page.tsx`
- Create: `frontend/src/app/globals.css`
- Create: `frontend/src/features/customer-intelligence/use-run-controller.ts`
- Create: `frontend/src/features/customer-intelligence/CustomerIntelligencePage.tsx`
- Create: `frontend/src/features/customer-intelligence/QueryComposer.tsx`
- Create: `frontend/src/features/customer-intelligence/AgentTrace.tsx`
- Create: `frontend/src/features/customer-intelligence/InsightSummary.tsx`
- Create: `frontend/src/features/customer-intelligence/RankedCustomers.tsx`
- Create: `frontend/src/features/customer-intelligence/JourneyTimeline.tsx`
- Create: `frontend/src/features/customer-intelligence/EvidenceDrawer.tsx`
- Create: `frontend/src/features/customer-intelligence/__tests__/CustomerIntelligencePage.test.tsx`

- [ ] **Step 1: 전체 사용자 흐름 실패 테스트 작성**

```typescript
it("runs a suggested question and opens customer evidence", async () => {
  const user = userEvent.setup();
  render(<CustomerIntelligencePage client={controlledClient} />);

  await user.click(screen.getByRole("button", { name: /검색 실패 후 상담 전환/ }));
  await user.click(screen.getByRole("button", { name: "분석 시작" }));
  controlledClient.emit(traceEvents);
  controlledClient.emit(resultEvent);

  expect(await screen.findByText("6명")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /CUST-003 Journey 보기/ }));
  expect(await screen.findByRole("heading", { name: /고객 Journey/ })).toBeInTheDocument();

  await user.click(screen.getAllByRole("button", { name: /근거 보기/ })[0]);
  expect(await screen.findByRole("dialog", { name: /Evidence/ })).toBeInTheDocument();
});
```

- [ ] **Step 2: Query와 Controller 구현**

빈 질문을 막고 추천 질문을 입력에 반영합니다.
실행 시 `POST /api/runs` 후 `fetch()` ReadableStream으로 SSE endpoint를 읽습니다.
새 실행은 기존 `AbortController`를 취소하고 이전 Run의 늦은 이벤트를 무시합니다.
Source chip은 최소 `search_history`를 유지하며 VOC는 비활성화할 수 있습니다.

- [ ] **Step 3: Trace와 Insight 구현**

Trace는 Plan과 Tool 이름, Source, 건수, 소요 시간만 표시하며 내부 추론을 표시하지 않습니다.
최신 상태 한 줄만 `aria-live="polite"`에 전달합니다.
Insight는 headline, 고객 수, 주요 Topic, Source 기여도, 제한 사항, 추천 Action을 표시합니다.

- [ ] **Step 4: Ranking, Journey와 Evidence Drawer 구현**

Ranking은 실제 `<table>`을 사용하고 위험도를 색과 한국어 텍스트로 표시합니다.
Journey는 시간순 `<ol>`과 `<time>`으로 렌더링합니다.
Drawer는 `role="dialog"`, `aria-modal="true"`, Escape 닫기, 열기 버튼으로 포커스 복귀를 구현합니다.

- [ ] **Step 5: 반응형 Visual System 구현**

1024px 이상에서는 Query/Trace와 Insight를 2열로, 그 아래 Ranking/Journey를 표시합니다.
작은 화면에서는 한 열로 쌓습니다.
Midnight navy 배경, warm white surface, cyan Source accent, amber risk accent를 사용합니다.
`prefers-reduced-motion`에서는 애니메이션을 끕니다.

- [ ] **Step 6: 컴포넌트 테스트와 Production build 확인**

Run: `npm --prefix frontend test -- --run && npm --prefix frontend run build`

Expected: 컴포넌트 테스트 통과와 Next.js build 성공

- [ ] **Step 7: UI 커밋**

```bash
git add frontend/src
git commit -m "feat: (frontend) 근거 기반 고객 Insight 화면 구현"
```

## Task 9: 선택적 DeepAgents와 Gemini Adapter

**Files:**
- Create: `backend/src/customer_signal/agent/gemini.py`
- Modify: `backend/src/customer_signal/runtime/coordinator.py`
- Create: `backend/tests/test_gemini_adapter.py`

- [ ] **Step 1: API key와 fallback contract 실패 테스트 작성**

```python
async def test_auto_mode_falls_back_and_records_reason(
    coordinator_factory,
    failing_gemini_runner,
):
    coordinator = coordinator_factory(
        mode="auto",
        gemini_runner=failing_gemini_runner,
    )

    snapshot = await coordinator.execute(valid_run_request)

    assert snapshot.status == "degraded"
    assert snapshot.agent_mode == "fixture"
    assert any(event.type == "fallback" for event in snapshot.events)
    assert snapshot.report.metrics[0].value == 6
```

- [ ] **Step 2: DeepAgent lazy 초기화 구현**

첫 Gemini Run에서만 `langchain-mcp-adapters` HTTP client로 `http://127.0.0.1:8000/mcp/` Tool을 읽고 Agent를 캐시합니다.
`TodoListMiddleware()`와 `response_format=InsightReport`를 사용합니다.
System prompt는 수치 계산 금지, Evidence 없는 claim 금지, 최대 6회 Tool 호출을 명시합니다.

- [ ] **Step 3: Mode와 실패 정책 구현**

`fixture`는 항상 fixture runner를 사용합니다.
`gemini`는 키 누락과 model 실패를 명시적 Run 오류로 반환합니다.
`auto`는 key 누락, 45초 timeout, provider 오류, 의미 검증 실패 때 fixture로 전환하고 사유를 공개 Trace에 기록합니다.

- [ ] **Step 4: Mock contract와 live marker 테스트**

Run: `uv run --project backend pytest backend/tests/test_gemini_adapter.py -q`

Expected: fake model Tool 선택, structured response, fallback 테스트 통과. 실제 provider 테스트는 `live` marker로 기본 실행에서 제외

- [ ] **Step 5: Agent Adapter 커밋**

```bash
git add backend/src/customer_signal/agent/gemini.py backend/src/customer_signal/runtime/coordinator.py backend/tests/test_gemini_adapter.py
git commit -m "feat: (agent) Gemini MCP 분석 모드 추가"
```

## Task 10: 실행 스크립트, E2E와 브라우저 QA

**Files:**
- Create: `Makefile`
- Create: `README.md`
- Create: `frontend/playwright.config.ts`
- Create: `frontend/e2e/working-demo.spec.ts`
- Modify: `backend/.env.example`
- Create: `frontend/.env.example`

- [ ] **Step 1: 실제 흐름 E2E 테스트 작성**

```typescript
test("question to evidence working demo", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /검색 실패 후 상담 전환/ }).click();
  await page.getByRole("button", { name: "분석 시작" }).click();
  await expect(page.getByText("6명")).toBeVisible();
  await page.getByRole("button", { name: /CUST-003 Journey 보기/ }).click();
  await expect(page.getByRole("heading", { name: /고객 Journey/ })).toBeVisible();
  await page.getByRole("button", { name: /근거 보기/ }).first().click();
  await expect(page.getByRole("dialog", { name: /Evidence/ })).toBeVisible();
});
```

- [ ] **Step 2: 한 명령 실행 경로 작성**

`make setup`, `make seed`, `make dev`, `make test`를 제공합니다.
`make dev`는 Backend 8000과 Frontend 3000을 함께 실행하고 종료 signal을 두 프로세스에 전달합니다.
README는 Python 3.12 설치, fixture 기본 모드, Gemini mode, 질문 예시와 예상 결과 6명을 설명합니다.

- [ ] **Step 3: 자동 검증 실행**

Run: `make test`

Expected: Backend pytest/Ruff, Frontend Vitest/build 전체 통과

- [ ] **Step 4: 실제 서버와 브라우저 검증**

Run: `make seed && make dev`

브라우저에서 다음을 확인합니다.

- 추천 질문과 자유 입력 실행
- Live Trace 순차 갱신
- 전체 Source 결과 6명
- VOC 비활성화 결과 0명과 제한 안내
- 고객 선택 시 Journey 교체
- Evidence Drawer 열기, Escape 닫기, 포커스 복귀
- 1280×800과 375×812에서 가로 페이지 스크롤 없음
- Backend 중단 시 재시도 가능한 오류 상태

- [ ] **Step 5: 최종 검증 커밋**

```bash
git add Makefile README.md backend/.env.example frontend/.env.example frontend/playwright.config.ts frontend/e2e
git commit -m "docs: (demo) 로컬 실행과 검증 경로 추가"
```

## 완료 조건

- `make setup && make seed && make test` 성공
- `make dev` 후 `http://127.0.0.1:3000`에서 질문부터 Evidence까지 완료
- API key가 없는 새 환경에서 fixture 전체 흐름 완료
- 전체 Source에서 6명, VOC 제외에서 0명 결과 재현
- 모든 Report 고객 ID, metric과 Evidence ID의 Run 사실 검증
- Backend와 Frontend 오류, 빈 결과, degraded 상태 렌더링
- 문서와 실제 명령, 포트, 환경변수 일치
