# 범용 고객 신호 분석과 실행 기록 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended)
> or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Adapter가 만든 공통 고객 Event를 자연어 목표에 따라 범용 분석 Primitive로 실행합니다.
검증된 단계별 분석 노트와 영속 문서 기록을 제공하는 워킹 프로토타입을 구현합니다.

**Architecture:** 모델은 질문을 typed `AnalysisGoal`과 최대 6단계 `AnalysisPlan`으로 구조화합니다.
서버 소유 `PrimitiveExecutor`가 각 단계를 실행해 immutable `AnalysisFact`를 만듭니다.
모델이 Fact 참조만 포함한 `AnalysisNoteDraft`를 반환하면 서버가 검증된 공개 Note와
`CustomerSignalReport`를 렌더링합니다. `RunStore`는 실시간 SSE를 담당하고 `ArtifactStore`는
같은 Run의 Goal, Plan, Fact, Note, 보고서를 JSON으로 원자 저장합니다.

**Tech Stack:** Python 3.12, Pydantic 2, DuckDB, FastAPI, FastMCP, Gemini `gemini-3.7-flash`, Next.js 16, React 19, TypeScript 5, Vitest, Playwright

---

## 파일 책임과 의존 순서

새 Backend 파일은 다음 책임만 가집니다.

| 파일 | 책임 |
| --- | --- |
| `domain/types.py` | 동적 `SourceId`, generic/legacy Primitive 이름과 공통 strict scalar의 단일 소유자 |
| `domain/sources.py` | Adapter, Manifest, Event scope와 필드 의미 계약 |
| `domain/primitives.py` | 허용 Primitive와 typed 입력 계약 |
| `domain/facts.py` | 서버 소유 Fact와 Metric provenance 계약 |
| `domain/analysis.py` | Goal, Plan, Step, Claim draft와 검증된 공개 Note 계약 |
| `data/source_registry.py` | Adapter 등록, Manifest 조회와 bounded Event 로드 |
| `synthetic/manifest.py` | 합성 5개 Source의 의미와 Capability 선언 |
| `synthetic/adapter.py` | DuckDB Repository를 `SourceAdapter`로 변환 |
| `analytics/primitives/*.py` | 집계, Segment, Sequence, Ranking, Journey 실행 |
| `analytics/executor.py` | Step 의존성, 비용 한도, Fact ID와 primitive dispatch |
| `agent/plan_validator.py` | Manifest에 대한 Plan과 revision 검증 |
| `agent/claim_validator.py` | Fact에 대한 Claim과 Note 검증 및 공개 문장 생성 |
| `agent/analysis_loop.py` | Goal부터 보고서까지 단계 실행과 checkpoint 조정 |
| `agent/generic_fixture.py` | 명시적 Demo 모드의 결정론적 Goal, Plan, Note 모델 |
| `agent/generic_gemini.py` | Gemini 구조화 Goal, Plan, Note, 보고서 draft 호출 |
| `runtime/artifacts.py` | 영속 Run Artifact와 목록, 문서 응답 계약 |
| `runtime/artifact_store.py` | UUID-safe 경로, 원자 저장, 목록과 복원 |
| `runtime/document_renderer.py` | Artifact에서 문서 View와 Markdown 파생 |

새 Frontend 파일은 다음 책임만 가집니다.

| 파일 | 책임 |
| --- | --- |
| `run-contract-decoders.ts` | 네트워크 경계의 legacy/generic 런타임 검증 |
| `source-catalog.ts` | 알려진 Source 표시 이름과 동적 ID fallback |
| `run-selectors.ts` | Fact 참조, Plan revision, legacy 중복 이벤트 선택 |
| `ChatPanel.tsx` | 질문, 확인 답변, 기간, Source와 추천 질문 |
| `RunHistory.tsx` | Artifact 목록과 이전 Run 선택 |
| `AnalysisWorkspace.tsx` | Goal, Plan, Note, 보고서와 legacy 화면 분기 |
| `AnalysisGoalCard.tsx` | 구조화 Goal 표시 |
| `AnalysisPlanView.tsx` | 현재 Plan과 Step 상태 표시 |
| `AnalysisNoteTimeline.tsx` | 검증된 공개 분석 노트 시간순 표시 |
| `FactDetail.tsx` | Metric, Source, result ID와 Evidence 표시 |
| `RunDocumentView.tsx` | 구조화 보고서 문서 View |
| `RunDownloads.tsx` | JSON과 Markdown 다운로드 링크 |
| `LegacyInsightWorkspace.tsx` | 기존 Journey 보고서 호환 경로 |

Backend golden JSON 계약이 확정되기 전에는 Frontend generic 계약을 구현하지 않습니다.

### Task 1: 공통 Event, Manifest와 Adapter 계약

**Files:**
- Create: `backend/src/customer_signal/domain/types.py`
- Create: `backend/src/customer_signal/domain/sources.py`
- Create: `backend/src/customer_signal/domain/primitives.py`
- Create: `backend/src/customer_signal/data/source_registry.py`
- Create: `backend/src/customer_signal/synthetic/manifest.py`
- Create: `backend/src/customer_signal/synthetic/adapter.py`
- Create: `backend/tests/support/__init__.py`
- Create: `backend/tests/support/in_memory_adapter.py`
- Create: `backend/tests/test_source_contracts.py`
- Create: `backend/tests/test_source_adapters.py`
- Modify: `backend/src/customer_signal/domain/models.py`
- Modify: `backend/src/customer_signal/domain/__init__.py`
- Modify: `backend/src/customer_signal/agent/contracts.py`
- Modify: `backend/src/customer_signal/data/repository.py`
- Modify: `backend/src/customer_signal/data/__init__.py`

- [ ] **Step 1: 공통 Event와 Manifest의 실패 테스트 작성**

```python
def test_manifest_rejects_undeclared_event_fields() -> None:
    manifest = synthetic_manifest("search_feedback")
    event = CanonicalCustomerEvent(
        event_id="EVT-1",
        evidence_id="EVD-1",
        source_id="search_feedback",
        canonical_customer_id="CUST-1",
        occurred_at=UTC_NOW,
        event_type="feedback",
        action="submit",
        topic="billing",
        outcome="negative",
        dimensions={"unknown": "x"},
        measures={"rating": 1},
        text="masked",
        attributes={},
        identities=[IDENTITY],
    )

    with pytest.raises(ValueError, match="undeclared dimension"):
        manifest.validate_event(event)
```

`test_source_contracts.py`에는 동적 `source_id`, finite measure, PII 분류, Capability와 반개구간 `EventScope` 검증도 각각 독립 테스트로 작성합니다.

- [ ] **Step 2: 계약 테스트 RED 확인**

Run: `uv run --project backend pytest backend/tests/test_source_contracts.py -q`

Expected: `ModuleNotFoundError: customer_signal.domain.sources`

- [ ] **Step 3: 공통 타입과 Manifest 구현**

```python
SourceId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$")]
DimensionValue = StrictStr | StrictInt | StrictBool | None
FiniteNumber = Annotated[float, Field(allow_inf_nan=False)]
MeasureValue = StrictInt | FiniteNumber
RefreshCadence = Literal["static_demo", "hourly", "daily", "weekly"]

class TimeRange(DomainModel):
    start_at: AwareDatetime
    end_at: AwareDatetime

    @model_validator(mode="after")
    def require_half_open_interval(self) -> Self:
        if self.start_at >= self.end_at:
            raise ValueError("start_at must be before exclusive end_at")
        return self

class EventScope(TimeRange):
    source_ids: list[SourceId] = Field(min_length=1, max_length=32)
    max_events: int = Field(ge=1, le=10_000)

    @field_validator("source_ids")
    @classmethod
    def require_unique_sources(cls, value: list[SourceId]) -> list[SourceId]:
        if len(value) != len(set(value)):
            raise ValueError("source_ids must be unique")
        return value

class DimensionDescriptor(DomainModel):
    semantic_type: Literal["category", "boolean", "identifier", "text"]
    description: str = Field(min_length=1)
    pii_classification: Literal["none", "quasi_identifier", "direct_identifier"]
    allowed_values: frozenset[str] | None = None

class MeasureDescriptor(DomainModel):
    semantic_type: Literal["integer", "number"]
    description: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    pii_classification: Literal["none", "quasi_identifier", "direct_identifier"] = "none"

class PublicDimensionDescriptor(DomainModel):
    semantic_type: Literal["category", "boolean", "identifier", "text"]
    description: str
    allowed_values: frozenset[str] | None = None

class PublicMeasureDescriptor(DomainModel):
    semantic_type: Literal["integer", "number"]
    description: str
    unit: str

class PublicSourceManifest(DomainModel):
    source_id: SourceId
    label: str
    description: str
    data_interval: TimeRange
    refresh_cadence: RefreshCadence
    supported_event_types: frozenset[str]
    supported_topics: frozenset[str]
    supported_outcomes: frozenset[str]
    dimensions: dict[str, PublicDimensionDescriptor]
    measures: dict[str, PublicMeasureDescriptor]
    capabilities: frozenset[GenericPrimitiveName]
    adapter_version: str
    manifest_version: str

class PublicSourceList(DomainModel):
    items: list[PublicSourceManifest]

class SourceManifest(DomainModel):
    source_id: SourceId
    label: str = Field(min_length=1)
    description: str = Field(min_length=1)
    adapter_version: str = Field(min_length=1)
    manifest_version: str = Field(min_length=1)
    data_interval: TimeRange
    refresh_cadence: RefreshCadence
    supported_event_types: frozenset[str]
    supported_topics: frozenset[str]
    supported_outcomes: frozenset[str]
    dimensions: dict[str, DimensionDescriptor]
    measures: dict[str, MeasureDescriptor]
    capabilities: frozenset[GenericPrimitiveName]
    masking_policy: MaskingPolicy
    identity_quality: IdentityQualityDescriptor

    def validate_event(self, event: CanonicalCustomerEvent) -> None:
        if event.source_id != self.source_id:
            raise ValueError("event source does not match manifest")
        unknown_dimensions = set(event.dimensions) - set(self.dimensions)
        unknown_measures = set(event.measures) - set(self.measures)
        if unknown_dimensions:
            raise ValueError("undeclared dimension")
        if unknown_measures:
            raise ValueError("undeclared measure")
```

`RefreshCadence`, `MaskingPolicy`, `IdentityQualityDescriptor`는 각각 갱신 주기, 공개 가능한
마스킹 수준, identity namespace와 link 방식/신뢰도 범위를 strict enum과 finite 값으로 고정합니다.
Manifest의 지원 event type, topic, outcome은 비어 있지 않아야 하고 `validate_event()`가 실제 Event 값도
해당 집합에 포함되는지 검사합니다. `CustomerEvent`에 strict `dimensions`와 finite `measures`를 추가합니다.
`TimeRange`와 descriptor는 `domain/sources.py`가 소유하고 `domain/analysis.py`가 import합니다.
Plan validator는 `pii_classification != "none"`인 field를 어떤 Predicate, Group, Measure에도 허용하지 않습니다.
`EventScope`는 aware 반개구간, unique dynamic Source ID와 hard event limit을 한 모델로 고정합니다.
`PublicDimensionDescriptor`와 `PublicMeasureDescriptor`는 semantic type, 설명과 unit만 포함합니다.
`PublicSourceManifest.from_internal()`은 `pii_classification == "none"`인 descriptor만 투영하고 PII field 이름,
masking rule의 내부 field map, identity namespace와 원천 컬럼을 출력하지 않습니다.
`CanonicalCustomerEvent = CustomerEvent` 호환 alias를 export합니다.
`GenericPrimitiveName`은 설계의 10개 이름만 포함합니다. `PrimitiveName`은 여기에 legacy
`match_journey_pattern` alias를 더한 호환 union입니다.
`SourceId`는 `domain/types.py`에서만 정의합니다.
두 Primitive 이름 alias도 `domain/types.py`에서만 정의해 `sources.py`와 `primitives.py` 사이의
순환 import를 막습니다. Generic `AnalysisStep`은 `GenericPrimitiveName`만 받습니다.
기존 `domain.models.SourceId`는 해당 alias를 re-export하고 `RunRequest.enabled_sources`도 같은 타입을 사용합니다.

Primitive 입력은 `primitive` discriminator를 가진 다음 strict 모델로 고정합니다.

| 입력 모델 | 필드 |
| --- | --- |
| `CatalogSourcesInput` | `primitive="catalog_sources"` |
| `ProfileEventsInput` | `primitive`, `group_by`, `predicates` |
| `AggregateEventsInput` | `primitive`, `aggregation`, `measure`, `group_by`, `predicates`, `time_grain` |
| `SegmentCustomersInput` | `primitive`, `predicates`, `minimum_matching_events` |
| `DetectRepetitionInput` | `primitive`, `topic_field`, `minimum_occurrences`, `within_hours` |
| `MatchSequenceInput` | `primitive`, `sequence` |
| `CompareSegmentsInput` | `primitive`, `metric_key` |
| `RankCustomersInput` | `primitive`, `weights`, `limit` |
| `GetCustomerJourneyInput` | `primitive`, `limit` |
| `GetEvidenceInput` | `primitive`, `limit` |

`PrimitiveInput`은 위 10개 모델의 discriminated union입니다.
`AnalysisStep.parameters.primitive`는 `AnalysisStep.primitive`와 일치해야 합니다.
Dependency Step ID는 `AnalysisStep.input_step_ids`에만 존재하며 Primitive parameter에 중복 저장하지 않습니다.

- [ ] **Step 4: Production Adapter와 In-memory Adapter 계약 테스트 작성**

```python
@pytest.mark.parametrize("adapter_factory", [duckdb_adapter_factory, memory_adapter_factory])
def test_adapter_returns_sorted_scoped_events_with_resolved_identity(adapter_factory) -> None:
    adapter = adapter_factory()
    events = list(adapter.load_events(SCOPE))
    assert events == sorted(events, key=lambda event: (event.occurred_at, event.event_id))
    assert all(SCOPE.start_at <= event.occurred_at < SCOPE.end_at for event in events)
    assert all(event.source_id == adapter.describe().source_id for event in events)
    edges = list(adapter.load_identities(SCOPE))
    for event in events:
        resolved = resolve_identities(event.identities, edges)
        assert resolved == {event.canonical_customer_id}
```

공유 Adapter 계약 테스트는 Manifest의 data coverage, refresh cadence, field 의미, masking policy와
identity namespace를 검증합니다. 모든 Event identity가 graph를 거쳐 정확히 한 canonical customer로
해소되어야 하며 0개 또는 2개 이상이면 실패합니다. Evidence retrieval은 `EvidenceProvider` Protocol로
분리하고, 두 구현에서 허용 ID만 마스킹된 `EvidenceRecord`로 반환하는 테스트를 추가합니다.

- [ ] **Step 5: Adapter 테스트 RED 확인**

Run: `uv run --project backend pytest backend/tests/test_source_adapters.py -q`

Expected: FAIL because `SyntheticDuckDBAdapter`, `InMemorySourceAdapter` and `SourceRegistry` are absent.

- [ ] **Step 6: Adapter, Registry와 Repository identity 조회 구현**

```python
class SourceAdapter(Protocol):
    def describe(self) -> SourceManifest: ...
    def load_events(self, scope: EventScope) -> Iterable[CanonicalCustomerEvent]: ...
    def load_identities(self, scope: EventScope) -> Iterable[IdentityEdge]: ...

class SourceRegistry:
    def __init__(self, adapters: Sequence[SourceAdapter], evidence: EvidenceProvider) -> None:
        self._adapters = {adapter.describe().source_id: adapter for adapter in adapters}
        if len(self._adapters) != len(adapters):
            raise ValueError("source adapters must be unique")

    def manifests(self, source_ids: Sequence[SourceId]) -> list[SourceManifest]:
        return [self._adapters[source_id].describe() for source_id in source_ids]
```

`SyntheticDuckDBAdapter`는 Source별 Manifest 한 개를 받아 기존 bounded Repository 조회를 감쌉니다.
`DuckDBRepository.list_identity_edges()`는 요청 Source와 기간의 Event에 연결된 Identity만 반환합니다.

- [ ] **Step 7: Task 1 GREEN과 회귀 확인**

Run: `uv run --project backend pytest backend/tests/test_source_contracts.py backend/tests/test_source_adapters.py backend/tests/test_generator.py backend/tests/test_database.py -q`

Expected: PASS

- [ ] **Step 8: Task 1 커밋**

```bash
git add backend/src/customer_signal/domain backend/src/customer_signal/data backend/src/customer_signal/synthetic \
  backend/src/customer_signal/agent/contracts.py backend/tests/support \
  backend/tests/test_source_contracts.py backend/tests/test_source_adapters.py
git commit -m "feat: (data) 공통 Event와 Source Adapter 계약 추가"
```

### Task 2: Goal, Plan, Fact, Claim과 검증기

**Files:**
- Create: `backend/src/customer_signal/domain/facts.py`
- Create: `backend/src/customer_signal/domain/analysis.py`
- Create: `backend/src/customer_signal/agent/plan_validator.py`
- Create: `backend/src/customer_signal/agent/claim_validator.py`
- Create: `backend/tests/test_analysis_contracts.py`
- Create: `backend/tests/test_plan_validator.py`
- Create: `backend/tests/test_claim_validator.py`
- Modify: `backend/src/customer_signal/domain/reports.py`
- Modify: `backend/src/customer_signal/agent/validator.py`

- [ ] **Step 1: strict 도메인 계약 테스트 작성**

```python
def test_plan_requires_three_to_six_acyclic_steps() -> None:
    with pytest.raises(ValidationError):
        AnalysisPlan(plan_id="plan-1", revision=0, goal_id="goal-1", steps=[STEP])

def test_note_draft_cannot_claim_server_owned_facts() -> None:
    with pytest.raises(ValidationError):
        AnalysisNoteDraft.model_validate({
            "step_id": "step-1",
            "claims": [],
            "next_step_id": "step-2",
            "facts": [{"fact_id": "forged"}],
        })

def test_fact_rejects_top_level_authorization_not_present_in_payload() -> None:
    with pytest.raises(ValidationError, match="payload projection"):
        AnalysisFact.model_validate({
            **VALID_FACT.model_dump(), "customer_ids": ["CUST-FORGED"],
        })
```

Goal clarification discriminated union, aware time range, unique Step/Fact ID, finite Metric,
typed Fact reference, 3~6단계와 strict extra rejection을 고정합니다.
`validate_goal_against_request()`는 Goal의 반개구간이 원 `RunRequest` 기간 안에 있고 Goal Source가
`enabled_sources`의 non-empty subset인지 검사합니다. 모델이 기간을 확장하거나 Source를 추가하면 거부합니다.

- [ ] **Step 2: 도메인 계약 RED 확인**

Run: `uv run --project backend pytest backend/tests/test_analysis_contracts.py -q`

Expected: `ModuleNotFoundError: customer_signal.domain.analysis`

- [ ] **Step 3: Goal, Plan, Fact와 Note 타입 구현**

```python
class AnalysisGoal(DomainModel):
    kind: Literal["goal"] = "goal"
    goal_id: str
    objective: str
    population: PopulationSpec
    time_range: TimeRange
    source_ids: list[SourceId] = Field(min_length=1, max_length=32)
    measures: list[MeasureSpec]
    group_by: list[FieldRef] = Field(default_factory=list)
    predicates: list[Predicate] = Field(default_factory=list)
    sequence: SequenceSpec | None = None
    output: OutputKind

class StepLimits(DomainModel):
    max_input_events: int = Field(ge=1, le=10_000)
    max_output_rows: int = Field(ge=1, le=100)
    max_evidence: int = Field(ge=0, le=20)
    timeout_seconds: float = Field(gt=0, le=40, allow_inf_nan=False)

class ExpectedOutputSpec(DomainModel):
    payload_kind: FactPayloadKind
    required_metric_keys: list[str] = Field(default_factory=list)

class ContinueAfterStep(DomainModel):
    kind: Literal["continue"] = "continue"

class StopOnEmpty(DomainModel):
    kind: Literal["stop_on_empty"] = "stop_on_empty"

class StopOnMetric(DomainModel):
    kind: Literal["stop_on_metric"] = "stop_on_metric"
    metric_key: str
    operator: Literal["eq", "lt", "lte", "gt", "gte"]
    target: FiniteFloat | int

StopCondition = Annotated[
    ContinueAfterStep | StopOnEmpty | StopOnMetric,
    Field(discriminator="kind"),
]

class AnalysisStep(DomainModel):
    step_id: str = Field(pattern=r"^step-[a-z0-9-]+$")
    primitive: GenericPrimitiveName
    parameters: PrimitiveInput
    source_ids: list[SourceId] = Field(min_length=1)
    input_step_ids: list[str] = Field(default_factory=list)
    expected_output: ExpectedOutputSpec
    stop_condition: StopCondition
    limits: StepLimits

class AnalysisPlan(DomainModel):
    plan_id: str = Field(min_length=1)
    revision: int = Field(ge=0)
    goal_id: str = Field(min_length=1)
    steps: list[AnalysisStep] = Field(min_length=3, max_length=6)

class ProcessingStats(DomainModel):
    scanned_events: int = Field(ge=0)
    matched_events: int = Field(ge=0)
    returned_rows: int = Field(ge=0)

class FactProvenance(DomainModel):
    scope: EventScope
    source_ids: list[SourceId]
    adapter_versions: dict[SourceId, str]
    manifest_versions: dict[SourceId, str]
    dataset_version: str

class AnalysisMetricFact(DomainModel):
    metric_key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    label: str = Field(min_length=1)
    value: FiniteFloat | int
    unit: str = Field(min_length=1)
    dimensions: dict[str, DimensionValue] = Field(default_factory=dict)

class FactPayloadBase(DomainModel):
    input_fact_ids: list[str] = Field(default_factory=list)
    processing: ProcessingStats
    provenance: FactProvenance
    metrics: list[AnalysisMetricFact]

class AnalysisFact(DomainModel):
    fact_id: str
    step_id: str
    primitive: GenericPrimitiveName
    result_id: str
    source_ids: list[SourceId]
    customer_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    metrics: list[AnalysisMetricFact] = Field(default_factory=list)
    payload: FactPayload
    created_at: AwareDatetime

class AnalysisNoteDraft(DomainModel):
    step_id: str
    claims: list[ClaimDraft]
    next_step_id: str | None
    limitations: list[str] = Field(default_factory=list)
```

`FactPayloadKind = GenericPrimitiveName`으로 고정하고, 다음 10개 strict payload가 모두
`FactPayloadBase`를 상속합니다. 표의 nested record도 `DomainModel(extra="forbid")`로 정의합니다.
각 payload 모델은 표의 discriminator와 같은 `kind: Literal[...]` 필드를 필수로 선언합니다.

| Payload discriminator/model | Primitive 고유 필드 |
| --- | --- |
| `catalog_sources` / `CatalogSourcesPayload` | `sources: list[AnalysisSourceCatalogFact]` |
| `profile_events` / `ProfileEventsPayload` | `distributions: list[AnalysisDistributionBucket]`, `data_quality: list[AnalysisQualityMetric]` |
| `aggregate_events` / `AggregateEventsPayload` | `buckets: list[AnalysisAggregateBucket]`, `series: list[AnalysisTimeBucket]` |
| `segment_customers` / `SegmentCustomersPayload` | `segment_id`, `customer_ids`, `predicate_counts` |
| `detect_repetition` / `RepetitionPayload` | `matches: list[AnalysisRepetitionMatch]` |
| `match_sequence` / `SequenceMatchPayload` | `matched_customer_ids`, `matches: list[AnalysisSequenceMatch]` |
| `compare_segments` / `SegmentComparisonPayload` | `baseline_fact_id`, `comparison_fact_id`, `deltas: list[AnalysisMetricDelta]` |
| `rank_customers` / `CustomerRankingPayload` | `customers: list[AnalysisRankedCustomer]` |
| `get_customer_journey` / `CustomerJourneyPayload` | `customer_id`, `events: list[AnalysisJourneyEvent]` |
| `get_evidence` / `EvidencePayload` | `records: list[AnalysisMaskedEvidence]` |

Nested record의 exact public 필드는 다음과 같습니다.

| Nested model | Strict 필드 |
| --- | --- |
| `AnalysisSourceCatalogFact` | `source_id`, `data_interval: TimeRange`, `row_count >= 0`, `manifest_version` |
| `AnalysisDistributionBucket` | `dimensions`, `event_count >= 0`, `customer_count >= 0`, `evidence_ids` |
| `AnalysisQualityMetric` | `field: FieldRef`, `missing_count`, `total_count`, `missing_rate: 0..1` |
| `AnalysisAggregateBucket` | `dimensions`, `metrics: list[AnalysisMetricFact]`, `event_count`, `customer_count`, `evidence_ids` |
| `AnalysisTimeBucket` | `time_range: TimeRange`, `dimensions`, `metrics`, `evidence_ids` |
| `AnalysisRepetitionMatch` | `customer_id`, `occurrence_count`, `window: TimeRange`, `evidence_ids` |
| `AnalysisSequenceMatch` | `customer_id`, `matched_event_ids`, `window: TimeRange`, `evidence_ids` |
| `AnalysisMetricDelta` | `metric_key`, `baseline`, `comparison`, `delta`, `unit` |
| `AnalysisRankedCustomer` | `customer_id`, `score: 0..100`, `signals: list[AnalysisSignal]`, `evidence_ids` |
| `AnalysisSignal` | `signal_key`, `label`, `contribution`, `metric_refs`, `evidence_ids` |
| `AnalysisJourneyEvent` | `event_id`, `evidence_id`, `source_id`, `occurred_at`, `event_type`, `action`, `topic`, `outcome`, masked `text` |
| `AnalysisMaskedEvidence` | `evidence_id`, `source_id`, `occurred_at`, `masked_customer_id`, `summary` |

```python
FactPayload = Annotated[
    CatalogSourcesPayload
    | ProfileEventsPayload
    | AggregateEventsPayload
    | SegmentCustomersPayload
    | RepetitionPayload
    | SequenceMatchPayload
    | SegmentComparisonPayload
    | CustomerRankingPayload
    | CustomerJourneyPayload
    | EvidencePayload,
    Field(discriminator="kind"),
]
```

각 nested record는 의미 key/unit, customer/evidence/source ID와 typed 값만 가집니다.
Generic nested model은 모두 `Analysis` prefix를 사용해 기존 legacy analytics model과 충돌하지 않습니다.
`AnalysisAggregateBucket`은 `dimensions`, `metrics`, `event_count`, `customer_count`, `evidence_ids`를 가집니다.
`AnalysisMaskedEvidence`는 마스킹된 summary만 허용하고 raw field나 원천 payload를 포함하지 않습니다.
공통 contract test는 ID list의 unique/stable order와 Step limit cardinality를 검사합니다. Catalog는
`source_id`, distribution/aggregate는 canonical dimensions, time/journey는 시각 뒤 ID, repetition은
`(-occurrence_count, customer_id)`, sequence/evidence는 요청 순서, ranking은 `(-score, customer_id)`로
정렬합니다. 같은 입력 replay에서 모든 nested record 순서가 같아야 합니다.
`processing`과 `provenance`는 10개 payload 모두 필수이며 Executor가 서버에서 채웁니다.
`metrics`도 10개 payload 모두 필수입니다. 각 Primitive는 canonical metric key/label/unit을 서버에서
생성하고 zero-result에도 value `0`인 Metric을 생략하지 않습니다. 최소 canonical key는
catalog `source_count`, profile `event_count`/`customer_count`, aggregate의 requested metric,
segment `segment_customer_count`, repetition `repeated_customer_count`, sequence `matched_customer_count`,
compare의 requested delta, ranking `ranked_customer_count`, journey `journey_event_count`, evidence
`evidence_record_count`입니다. `AnalysisFact.metrics`는 `payload.metrics`와 exact 같아야 합니다.
실제 모듈에서는 10개 payload와 `FactPayload` union을 `AnalysisFact`보다 먼저 정의합니다.
Generic `AnalysisMetricFact`는 legacy `agent.contracts.MetricFact`와 별도 타입입니다. Claim `FactRef`와
`ExpectedOutputSpec.required_metric_keys`는 이 generic `metric_key`만 참조해 기존 Journey 계약을 깨지 않습니다.
`extract_fact_projection(payload)`는 payload-kind별 server extractor입니다. Nested records에서 customer,
evidence ID와 Metric을 stable order로 unique 추출합니다. `build_fact()`는 `source_ids`를 항상 non-empty
restricted Step scope에서 채우고 나머지 top-level 값을 projection에서 채웁니다. `AnalysisFact` validator는
top-level customer/evidence/metric이 extractor 결과와 exact 같은지, source/provenance가 restricted scope와
같은지, nested record의 모든 source ID가 그 scope subset인지 다시 검사합니다. 빈 결과에서도 consulted
Source는 top-level과 provenance에 남습니다.
Top-level ID/Metric을 조작하거나 payload와 다른 authorization을 만들면 ValidationError가 나야 합니다.

`AnalysisStep.parameters` discriminator는 `primitive`와 같아야 합니다. `input_step_ids`는 현재 Step보다
앞선 Step만 가리키고, Executor가 해당 Step이 만든 immutable Fact ID로 exact 변환합니다.
Dependency arity는 compare=2, rank=1~4, journey=1, evidence=1, 나머지 raw-event Primitive=0으로
검증합니다. baseline/comparison은 compare의 ordered `input_step_ids[0:2]`로 해석합니다.
`expected_output`은 handler 반환 뒤 Fact 공개 전에 payload kind와 필수 metric key를 검증합니다.
`stop_condition`은 검증된 현재 Fact에만 평가하며 임의 표현식이나 model prose를 실행하지 않습니다.
Plan validator는 step ID와 dependency의 고유성, source subset, topological order를 함께 검사합니다.
또한 `expected_output.payload_kind == step.primitive`와 `StopOnMetric.metric_key`가
`expected_output.required_metric_keys`에 포함되는지를 실행 전에 검사합니다.

`RunStatus`도 이 low-level domain 모듈에서
`queued | running | awaiting_clarification | completed | degraded | failed`의 단일 union으로 정의하고
Backend outcome, runtime snapshot, Artifact가 모두 re-export해 사용합니다.
`PublicRunError`도 같은 모듈에 `code`, safe `message`, optional `step_id`,
`suggested_questions`만 가진 strict 계약으로 정의해 agent와 runtime 사이의 순환 import를 막습니다.
모델 소유 `ClaimDraft`는 `claim_type`, `subject`, `operator`, `target`, `fact_refs`만 허용하고 ID를 받지 않습니다.
서버는 검증 뒤 canonicalized operands와 Fact ID hash로 stable `claim_id`를 만든 `VerifiedClaim`으로 변환합니다.
같은 Run의 `claim_id`는 고유해야 합니다. 공개 `AnalysisNote`만 `VerifiedClaim`, 서버 소유 Fact, Source,
result/evidence ID, 시각과 duration을 가집니다.

같은 모듈에 다음 orchestration 계약을 정의합니다.

```python
class ClarificationRequired(DomainModel):
    kind: Literal["clarification"] = "clarification"
    clarification_id: str
    question: str = Field(min_length=1)

class UnsupportedAnalysis(DomainModel):
    kind: Literal["unsupported"] = "unsupported"
    code: Literal["pii_request", "raw_export", "write_request", "unsupported_statistic", "out_of_scope"]
    reason: str = Field(min_length=1)
    suggested_questions: list[str] = Field(min_length=1, max_length=3)

GoalDecision = Annotated[
    AnalysisGoal | ClarificationRequired | UnsupportedAnalysis,
    Field(discriminator="kind"),
]

class ContinueSelection(DomainModel):
    kind: Literal["continue"] = "continue"
    next_step_id: str

class StopSelection(DomainModel):
    kind: Literal["stop"] = "stop"

class ReviseSelection(DomainModel):
    kind: Literal["revise"] = "revise"
    revised_plan: AnalysisPlan
    next_step_id: str

StepSelection = Annotated[
    ContinueSelection | StopSelection | ReviseSelection,
    Field(discriminator="kind"),
]

class CustomerSignalReportDraft(DomainModel):
    goal_id: str
    claim_refs: list[str]
    recommended_actions: list[RecommendedActionDraft]
```

`StepSelection` 세 variant는 상호 배타적입니다. `ContinueSelection.next_step_id`와
`ReviseSelection.next_step_id`는 dependency가 충족된 미완료 Step이어야 합니다. `StopSelection`은
Goal의 required output/metric이 현재 Fact로 충족될 때만 허용하고, 그렇지 않으면 fail closed합니다.

`CustomerSignalReport`는 `report_kind="customer_signal"`, Goal, server-owned Metric/Signal/Ranking/Journey,
검증된 Finding, Recommendation, limitation과 provenance를 가집니다.
기존 `InsightReport`에는 `report_kind="legacy_journey"`를 추가해 discriminated union으로 유지합니다.

- [ ] **Step 4: Plan과 revision 공격 테스트 작성**

```python
def test_revision_cannot_replace_completed_step() -> None:
    with pytest.raises(PlanValidationError, match="completed step is immutable"):
        validate_plan_revision(
            previous=PLAN,
            revised=PLAN_WITH_CHANGED_STEP_ONE,
            completed_step_ids={"step-1"},
            manifests=MANIFESTS,
        )
```

원 요청보다 이른 시작, 늦은 종료, 빈 Source, 비활성 Source와 공개 Goal/실행 Scope 불일치를 각각
`validate_goal_against_request()`에서 거부하는 공격 테스트를 추가합니다.
Unknown Source/field/Capability, PII field, cycle, forward dependency, dependency arity, 중복 ID,
6단계 초과, 반환 행/시간/Evidence 한도 초과를 각각 실패시키는 테스트를 작성합니다.

- [ ] **Step 5: Claim 의미 변조 공격 테스트 작성**

```python
def test_same_number_with_different_metric_key_is_rejected() -> None:
    fact = metric_fact(metric_key="matched_customer_count", value=6, unit="customers")
    claim = ClaimDraft(
        claim_type="metric",
        subject="revenue",
        operator="eq",
        target=6,
        fact_refs=[FactRef(fact_id=fact.fact_id, metric_key="matched_customer_count")],
    )
    with pytest.raises(ClaimValidationError, match="subject does not match metric"):
        validate_claim(claim, facts=[fact])
```

Wrong unit, operator, Segment, 고객, Source, Evidence, stale revision과 cross-Fact 조합도 차단합니다.
PII 조회, raw export, 쓰기 요청과 Manifest/Primitive로 표현할 수 없는 통계는 `UnsupportedAnalysis`로만
표현되며 executable Plan으로 바뀌지 않는 계약 테스트를 추가합니다.

- [ ] **Step 6: Plan과 Claim 공격 테스트 RED 확인**

Run: `uv run --project backend pytest backend/tests/test_plan_validator.py backend/tests/test_claim_validator.py -q`

Expected: FAIL because plan/revision and exact claim validation are not implemented.

- [ ] **Step 7: PlanValidator와 ClaimValidator 구현**

```python
def validate_plan(plan: AnalysisPlan, manifests: Sequence[SourceManifest]) -> None:
    manifest_by_source = {manifest.source_id: manifest for manifest in manifests}
    _validate_unique_steps(plan.steps)
    _validate_acyclic_dependencies(plan.steps)
    for step in plan.steps:
        _validate_step_sources(step, manifest_by_source)
        _validate_step_fields(step, manifest_by_source)
        _validate_step_capability(step, manifest_by_source)
        _validate_limits(step.limits)

def render_verified_note(draft: AnalysisNoteDraft, fact: AnalysisFact, duration_ms: int) -> AnalysisNote:
    claims = [validate_and_render_claim_draft(claim, [fact]) for claim in draft.claims]
    return AnalysisNote.from_server_fact(draft=draft, fact=fact, claims=claims, duration_ms=duration_ms)
```

- [ ] **Step 8: Task 2 GREEN과 기존 validator 회귀 확인**

Run: `uv run --project backend pytest backend/tests/test_analysis_contracts.py backend/tests/test_plan_validator.py backend/tests/test_claim_validator.py backend/tests/test_validator.py -q`

Expected: PASS

- [ ] **Step 9: Task 2 커밋**

```bash
git add backend/src/customer_signal/domain \
  backend/src/customer_signal/agent/plan_validator.py \
  backend/src/customer_signal/agent/claim_validator.py \
  backend/src/customer_signal/agent/validator.py \
  backend/tests/test_analysis_contracts.py \
  backend/tests/test_plan_validator.py \
  backend/tests/test_claim_validator.py
git commit -m "feat: (agent) 범용 분석 계획과 Claim 검증 계약 추가"
```

### Task 3: 합성 데이터와 DuckDB 공통 Event 확장

**Files:**
- Modify: `backend/src/customer_signal/data/database.py`
- Modify: `backend/src/customer_signal/data/repository.py`
- Modify: `backend/src/customer_signal/synthetic/generator.py`
- Modify: `backend/src/customer_signal/synthetic/manifest.py`
- Modify: `backend/tests/test_generator.py`
- Modify: `backend/tests/test_database.py`
- Modify: `backend/tests/test_source_adapters.py`

- [ ] **Step 1: 세 개 수용 목표의 데이터 RED 테스트 작성**

```python
def test_dataset_contains_distinct_generic_analysis_patterns() -> None:
    dataset = generate_dataset(seed=20260819)
    events = dataset.events
    assert count_negative_feedback_topic(events, "요금제 변경") == 6
    assert count_repeat_to_voc_customers(events) == 6
    assert count_signup_started_not_completed(events) == 5
```

가입 시작 고객 12명, 완료 고객 7명, 미완료 고객 5명을 Event와 Evidence로 고정합니다. 기존 반복 검색 후 VOC 6명과 near-miss 의미는 유지합니다.

- [ ] **Step 2: 데이터 테스트 RED 확인**

Run: `uv run --project backend pytest backend/tests/test_generator.py -q`

Expected: FAIL because signup start/completion events and normalized measures are absent.

- [ ] **Step 3: Generator와 Manifest 확장**

```python
for index, customer_id in enumerate(customer_ids[:12]):
    add_event(customer_id, source_id="subscription", event_type="signup", action="started",
              topic="가입", outcome="pending", dimensions={"stage": "application"}, measures={})
    if index < 7:
        add_event(customer_id, source_id="subscription", event_type="signup", action="completed",
                  topic="가입", outcome="success", dimensions={"stage": "activated"}, measures={})
```

기존 `rating`, `result_count`, `session_depth`는 `attributes` 호환값을 유지하고 Manifest에 선언된 `measures`에도 복제합니다.

- [ ] **Step 4: DuckDB schema migration RED 테스트 작성**

```python
def test_current_database_persists_dimensions_measures_and_versions(tmp_path: Path) -> None:
    path = tmp_path / "signals.duckdb"
    seed_database(path, generate_dataset(seed=20260819))
    columns = describe_columns(path, "events")
    assert columns["dimensions"] == "JSON"
    assert columns["measures"] == "JSON"
    assert metadata(path)["manifest_version"] == SYNTHETIC_MANIFEST_VERSION
```

구형 schema와 잘못된 JSON 타입을 startup에서 atomic reseed하는 테스트도 추가합니다.

- [ ] **Step 5: DB migration 테스트 RED 확인**

Run: `uv run --project backend pytest backend/tests/test_database.py backend/tests/test_source_adapters.py -q`

Expected: FAIL because dimensions/measures JSON columns and manifest metadata are absent.

- [ ] **Step 6: DB schema, Repository projection과 metadata 구현**

`events`에 `dimensions JSON NOT NULL`, `measures JSON NOT NULL`을 추가하고 schema/dataset version을 올립니다.
Repository는 JSON을 strict Event로 복원합니다.
Readiness manifest는 다섯 테이블과 새 컬럼 타입을 정확히 검사합니다.

- [ ] **Step 7: Task 3 GREEN과 회귀 확인**

Run: `uv run --project backend pytest backend/tests/test_generator.py backend/tests/test_database.py backend/tests/test_source_adapters.py backend/tests/test_analytics.py -q`

Expected: PASS with updated deterministic row counts.

- [ ] **Step 8: Task 3 커밋**

```bash
git add backend/src/customer_signal/data backend/src/customer_signal/synthetic backend/tests/test_generator.py backend/tests/test_database.py backend/tests/test_source_adapters.py backend/tests/test_analytics.py
git commit -m "feat: (data) 범용 분석용 Event 차원과 가입 패턴 추가"
```

### Task 4: 범용 분석 Primitive와 Fact Executor

**Files:**
- Create: `backend/src/customer_signal/analytics/primitives/__init__.py`
- Create: `backend/src/customer_signal/analytics/primitives/common.py`
- Create: `backend/src/customer_signal/analytics/primitives/profile.py`
- Create: `backend/src/customer_signal/analytics/primitives/segments.py`
- Create: `backend/src/customer_signal/analytics/primitives/sequences.py`
- Create: `backend/src/customer_signal/analytics/primitives/ranking.py`
- Create: `backend/src/customer_signal/analytics/primitives/evidence.py`
- Create: `backend/src/customer_signal/analytics/executor.py`
- Create: `backend/tests/test_analytics_profile.py`
- Create: `backend/tests/test_analytics_segments.py`
- Create: `backend/tests/test_analytics_sequences.py`
- Create: `backend/tests/test_primitive_contracts.py`
- Create: `backend/tests/test_primitive_executor.py`
- Modify: `backend/src/customer_signal/analytics/__init__.py`
- Test: `backend/tests/test_mcp_server.py`

- [ ] **Step 1: profile, aggregate와 Segment RED 테스트 작성**

```python
def test_negative_feedback_topic_plan_returns_fact_with_semantic_metric() -> None:
    fact = executor.execute(
        NEGATIVE_FEEDBACK_AGGREGATE_STEP, scope=SCOPE, prior_facts=[], budget=TEST_BUDGET,
    )
    assert fact.primitive == "aggregate_events"
    assert fact.metrics[0].metric_key == "negative_feedback_customer_count"
    assert fact.metrics[0].unit == "customers"
    assert fact.payload.buckets[0].dimensions["topic"] == "요금제 변경"
```

Profile 결측률, event/customer distinct count, event predicate의 AND 의미, Segment membership과 compare baseline을 별도 테스트로 고정합니다.

- [ ] **Step 2: 반복, Sequence와 가입 이탈 RED 테스트 작성**

```python
def test_sequence_and_signup_abandonment_have_distinct_results() -> None:
    repeat_fact = executor.execute(
        REPEAT_TO_VOC_STEP, scope=SCOPE, prior_facts=[], budget=TEST_BUDGET,
    )
    signup_fact = executor.execute(
        SIGNUP_ABANDONMENT_STEP, scope=SCOPE, prior_facts=[], budget=TEST_BUDGET,
    )
    assert repeat_fact.metric("matched_customer_count").value == 6
    assert signup_fact.metric("abandoned_customer_count").value == 5
    assert repeat_fact.customer_ids != signup_fact.customer_ids
```

- [ ] **Step 3: Primitive 테스트 RED 확인**

Run: `uv run --project backend pytest backend/tests/test_analytics_profile.py backend/tests/test_analytics_segments.py backend/tests/test_analytics_sequences.py -q`

Expected: import failure for `customer_signal.analytics.primitives`

- [ ] **Step 4: Profile, Aggregate, Segment와 Sequence 순수 함수 구현**

```python
PrimitiveHandler = Callable[[PrimitiveContext, DomainModel], FactPayloadBase]

@dataclass(frozen=True)
class HandlerSpec:
    input_type: type[DomainModel]
    output_type: type[FactPayloadBase]
    handler: PrimitiveHandler

CORE_HANDLERS: dict[GenericPrimitiveName, HandlerSpec] = {
    "profile_events": HandlerSpec(ProfileEventsInput, ProfileEventsPayload, profile_events),
    "aggregate_events": HandlerSpec(AggregateEventsInput, AggregateEventsPayload, aggregate_events),
    "segment_customers": HandlerSpec(SegmentCustomersInput, SegmentCustomersPayload, segment_customers),
    "detect_repetition": HandlerSpec(DetectRepetitionInput, RepetitionPayload, detect_repetition),
    "match_sequence": HandlerSpec(MatchSequenceInput, SequenceMatchPayload, match_sequence),
    "compare_segments": HandlerSpec(CompareSegmentsInput, SegmentComparisonPayload, compare_segments),
}
```

이 단계에서는 첫 RED를 통과하는 core handler만 구현합니다. 모든 handler는 전체 scoped Event를 확인한 뒤
결과 한도를 적용합니다. 입력 행이 실행 예산을 넘으면 일부 행으로 분석하지 않고
`PrimitiveLimitError`를 발생시킵니다.

- [ ] **Step 5: 10개 Primitive 공통 행동 RED 테스트 작성과 확인**

`test_primitive_contracts.py`는 정확히 10개 handler를 parameterize하여 빈 입력, 허용 Source,
결과 한도, provenance, 같은 입력 반복 실행을 모두 검증합니다. `catalog_sources`, `rank_customers`,
`get_customer_journey`, `get_evidence`도 별도 대표 payload assertion을 가집니다.
각 handler의 Fact Source/provenance가 `step.source_ids`로 제한되고 Goal-wide 다른 Source가 섞이지 않는
교차 Source 테스트도 추가합니다.

Run: `uv run --project backend pytest backend/tests/test_primitive_contracts.py -q`

Expected: FAIL because the complete registry and handlers are not implemented.

- [ ] **Step 6: Executor dependency, stable ID와 실행 예산 RED 테스트 작성**

```python
def test_repeated_primitive_calls_have_distinct_fact_ids_bound_to_dependencies() -> None:
    first = executor.execute(
        AGGREGATE_BY_TOPIC, scope=SCOPE, prior_facts=[], budget=TEST_BUDGET,
    )
    second = executor.execute(
        AGGREGATE_BY_SOURCE, scope=SCOPE, prior_facts=[first], budget=TEST_BUDGET,
    )
    assert first.fact_id != second.fact_id
    assert second.payload.input_fact_ids == [first.fact_id]
    replay = executor.execute(
        AGGREGATE_BY_SOURCE, scope=SCOPE, prior_facts=[first], budget=TEST_BUDGET,
    )
    assert replay.fact_id == second.fact_id
    assert replay.result_id == second.result_id
    assert replay.payload == second.payload

def test_fact_id_changes_when_restricted_scope_changes() -> None:
    first = executor.execute(STEP, scope=AUGUST_SCOPE, prior_facts=[], budget=TEST_BUDGET)
    second = executor.execute(STEP, scope=JULY_SCOPE, prior_facts=[], budget=TEST_BUDGET)
    source_changed = executor.execute(STEP_WITH_OTHER_SOURCE, scope=AUGUST_SCOPE,
                                      prior_facts=[], budget=TEST_BUDGET)
    assert len({first.fact_id, second.fact_id, source_changed.fact_id}) == 3
```

고정 Clock을 주입해 ID와 payload만 결정적으로 비교합니다. 취소, timeout, unknown dependency,
row/evidence budget, 미완료 Fact 비공개도 테스트합니다.

```python
def test_slow_primitive_observes_run_deadline_without_publishing_fact() -> None:
    budget = RunBudget(deadline_monotonic=clock.monotonic() + 0.01)
    with pytest.raises(PrimitiveTimeoutError):
        executor.execute(SLOW_STEP, scope=SCOPE, prior_facts=[], budget=budget)
    assert published_facts == []

@pytest.mark.parametrize("payload", [TOO_MANY_ROWS, TOO_MANY_EVIDENCE, WRONG_PAYLOAD_KIND, MISSING_METRIC])
def test_invalid_or_over_budget_payload_never_becomes_a_fact(payload) -> None:
    executor = executor_with_handler_result(payload)
    with pytest.raises((PrimitiveLimitError, PrimitiveContractError)):
        executor.execute(STEP, scope=SCOPE, prior_facts=[], budget=TEST_BUDGET)
    assert published_facts == []
```

Run: `uv run --project backend pytest backend/tests/test_primitive_executor.py -q`

Expected: FAIL because `PrimitiveExecutor` and `RunBudget` are absent.

- [ ] **Step 7: 남은 Primitive, Executor와 cancellation budget 구현**

```python
class PrimitiveExecutor:
    def execute(self, step: AnalysisStep, *, scope: EventScope,
                prior_facts: Sequence[AnalysisFact], budget: RunBudget) -> AnalysisFact:
        inputs = resolve_dependencies(step, prior_facts)
        step_scope = scope.restrict(
            step.source_ids,
            max_events=min(scope.max_events, step.limits.max_input_events),
        )
        step_budget = budget.child(timeout_seconds=step.limits.timeout_seconds)
        step_budget.checkpoint()
        context = self._context(step_scope, inputs, step_budget)
        spec = HANDLERS[step.primitive]
        parameters = spec.input_type.model_validate(step.parameters.model_dump())
        raw_payload = spec.handler(context, parameters)
        payload = spec.output_type.model_validate(raw_payload)
        step_budget.checkpoint()
        if payload.processing.scanned_events == 0:
            raise NoDataScope(payload.provenance)
        _validate_payload_limits(
            payload,
            max_output_rows=step.limits.max_output_rows,
            max_evidence=step.limits.max_evidence,
        )
        _validate_expected_output(payload, step.expected_output)
        return build_fact(
            step, payload, self._version_context, inputs, scope=step_scope,
        )

    async def execute_async(self, step: AnalysisStep, *, scope: EventScope,
                            prior_facts: Sequence[AnalysisFact], budget: RunBudget) -> AnalysisFact:
        try:
            return await asyncio.to_thread(
                self.execute, step, scope=scope, prior_facts=prior_facts, budget=budget,
            )
        except asyncio.CancelledError:
            budget.cancel()
            raise
```

이 단계에서 `catalog_sources`, `rank_customers`, `get_customer_journey`, `get_evidence`와 정확히
10개인 최종 `HANDLERS: dict[GenericPrimitiveName, HandlerSpec]` registry를 구현합니다.
Registry contract test는 각 key의 input discriminator, output kind와 handler 반환 type을 교차검증하고
다른 Primitive input/output을 주입하면 handler 전/Fact build 전에 실패하는지 확인합니다.
`resolve_dependencies()`는 `step.input_step_ids`를 `prior_facts[*].step_id`에 매핑하고 누락, 중복,
미래 Step을 거부한 뒤 ordered Fact list를 handler context에 전달합니다. Handler 반환은
`step.expected_output`을 통과한 다음에만 `AnalysisFact`로 생성합니다.
Executor는 Goal scope를 직접 handler에 넘기지 않습니다. 먼저
`step_scope = scope.restrict(step.source_ids, max_events=step.limits.max_input_events)`를 만들고,
Step Source가 Goal Source subset인지와 hard Run limit 이하인지 검사합니다. Handler context, Fact의
`source_ids`, payload provenance scope는 모두 이 restricted scope와 exact 일치해야 합니다.
`result_id`와 `fact_id` hash에는 Adapter, Dataset, Manifest version, primitive, step ID, Step parameters,
input Fact ID, relevant Step limits와 canonical restricted scope의 start/end/source_ids/max_events를 포함합니다.
같은 입력과 scope replay는 같은 ID이고 기간이나 Source가 다르면 다른 ID여야 합니다.
`RunBudget`은 monotonic deadline과 cancellation flag를 소유하고 `PrimitiveContext`로 전달됩니다.
Production과 test 모두 budget을 명시적으로 주입하며 무제한 default는 두지 않습니다.
각 handler는 Event scan과 결과 materialization loop에서 주기적으로 `budget.checkpoint()`를 호출합니다.
`AnalysisLoop`는 동기 `execute()`를 Event loop에서 직접 호출하지 않고 `execute_async()`만 await합니다.
따라서 Task 8 Coordinator의 `asyncio.timeout()`이나 shutdown 취소가 즉시 budget flag를 설정하고,
worker thread의 handler가 다음 checkpoint에서 중단됩니다. Fact publication은 await가 정상 반환한 뒤에만 일어나므로
timeout/cancel 이후 background thread가 결과를 공개할 수 없습니다. 테스트는 blocking fake handler가 시작된 뒤
timeout/cancel을 주입하고 worker가 `PrimitiveCancelledError`로 끝나는 것까지 기다립니다.

- [ ] **Step 8: 내부 Primitive Registry와 기존 FastMCP 호환 검증**

`PrimitiveExecutor.handlers`가 정확히 10개 generic Primitive를 갖는지 검증합니다.
기존 FastMCP 6개 Tool은 legacy Journey 회귀를 위해 그대로 유지합니다.
SQL, 파일, 쓰기 Tool이 없는지도 재확인합니다.
Generic Analysis Loop는 `PrimitiveExecutor`를 권위 실행 경로로 사용합니다.

- [ ] **Step 9: Task 4 GREEN과 회귀 확인**

Run:

```bash
uv run --project backend pytest \
  backend/tests/test_analytics_profile.py \
  backend/tests/test_analytics_segments.py \
  backend/tests/test_analytics_sequences.py \
  backend/tests/test_primitive_contracts.py \
  backend/tests/test_primitive_executor.py \
  backend/tests/test_mcp_server.py -q
```

Expected: PASS

- [ ] **Step 10: Task 4 커밋**

```bash
git add backend/src/customer_signal/analytics \
  backend/tests/test_analytics_profile.py \
  backend/tests/test_analytics_segments.py \
  backend/tests/test_analytics_sequences.py \
  backend/tests/test_primitive_contracts.py \
  backend/tests/test_primitive_executor.py \
  backend/tests/test_mcp_server.py
git commit -m "feat: (analytics) 범용 고객 신호 Primitive 실행 추가"
```

### Task 5: Run Artifact 원자 저장과 문서 렌더링

**Files:**
- Create: `backend/src/customer_signal/runtime/artifacts.py`
- Create: `backend/src/customer_signal/runtime/artifact_store.py`
- Create: `backend/src/customer_signal/runtime/document_renderer.py`
- Create: `backend/tests/test_artifact_store.py`
- Create: `backend/tests/test_document_renderer.py`
- Modify: `backend/src/customer_signal/config.py`
- Modify: `.gitignore`

- [ ] **Step 1: Artifact atomic persistence RED 테스트 작성**

```python
def test_store_round_trips_partial_and_completed_artifacts_atomically(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    store.save(PARTIAL_ARTIFACT)
    assert store.load(PARTIAL_ARTIFACT.run_id) == PARTIAL_ARTIFACT
    store.save(COMPLETED_ARTIFACT)
    assert store.load(COMPLETED_ARTIFACT.run_id) == COMPLETED_ARTIFACT
    assert list(tmp_path.glob("*.tmp")) == []
```

잘못된 UUID path traversal, unsupported schema version, 쓰기 실패 시 기존 파일 보존, 최신 `updated_at` 정렬과 생성자 import-no-write를 테스트합니다.

- [ ] **Step 2: Artifact 테스트 RED 확인**

Run: `uv run --project backend pytest backend/tests/test_artifact_store.py -q`

Expected: import failure for `customer_signal.runtime.artifact_store`

- [ ] **Step 3: Artifact 계약과 Store 구현**

```python
class RunArtifact(DomainModel):
    schema_version: Literal[1] = 1
    run_id: UUID
    status: RunStatus
    created_at: AwareDatetime
    updated_at: AwareDatetime
    completed_at: AwareDatetime | None = None
    request: RunRequest
    goal: AnalysisGoal | None = None
    clarification: ClarificationRecord | None = None
    plan: AnalysisPlan | None = None
    facts: list[AnalysisFact] = Field(default_factory=list)
    notes: list[AnalysisNote] = Field(default_factory=list)
    report: CustomerSignalReport | InsightReport | None = None
    last_event_id: int = 0
    versions: RunVersions
    failed_step_id: str | None = None
    limitations: list[str] = Field(default_factory=list)
    error: PublicRunError | None = None

class ArtifactStore:
    def save(self, artifact: RunArtifact) -> None:
        target = self._path(artifact.run_id)
        self._directory.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(f".{uuid4().hex}.tmp")
        temp.write_text(artifact.model_dump_json(indent=2), encoding="utf-8")
        os.replace(temp, target)
```

Artifact validator는 `created_at <= updated_at`, terminal 상태의 `completed_at`, failed 상태의 safe
`error`, `failed_step_id`의 현재 Plan membership을 검사합니다. `awaiting_clarification`, partial failure,
degraded no-data Artifact를 각각 round-trip하고 `updated_at` 최신순 목록 정렬을 확인합니다.
저장 전 public-data validator로 `raw_fields`, Provider message, Secret, unmasked identity와 금지 키를 재귀 검사합니다.

- [ ] **Step 4: 문서 파생 RED 테스트 작성**

```python
def test_document_and_markdown_only_render_artifact_facts() -> None:
    document = render_document(COMPLETED_ARTIFACT)
    markdown = render_markdown(COMPLETED_ARTIFACT)
    assert document.headline == COMPLETED_ARTIFACT.report.headline
    assert "6명" in markdown
    assert "raw_fields" not in markdown
    assert document.provenance.result_ids == artifact_result_ids(COMPLETED_ARTIFACT)
```

- [ ] **Step 5: Document renderer 테스트 RED 확인**

Run: `uv run --project backend pytest backend/tests/test_document_renderer.py -q`

Expected: FAIL because `document_renderer` and derived document contracts are absent.

- [ ] **Step 6: 순수 document renderer 구현**

`ArtifactDocument`는 질문, 범위, Goal, Plan, Notes, 보고서, provenance와 limitation 섹션만 가집니다.
Renderer는 DuckDB, 모델과 외부 파일을 읽지 않고 입력 Artifact에서만 값을 복사하거나 형식화합니다.

- [ ] **Step 7: Task 5 GREEN과 lint 확인**

Run: `uv run --project backend pytest backend/tests/test_artifact_store.py backend/tests/test_document_renderer.py -q`

Run: `uv run --project backend ruff check backend`

Expected: PASS and `All checks passed!`

- [ ] **Step 8: Task 5 커밋**

```bash
git add .gitignore backend/src/customer_signal/runtime backend/src/customer_signal/config.py backend/tests/test_artifact_store.py backend/tests/test_document_renderer.py
git commit -m "feat: (runtime) 분석 Run Artifact와 문서 기록 추가"
```

### Task 6: 서버 소유 Analysis Loop와 명시적 Fixture 모델

**Files:**
- Create: `backend/src/customer_signal/agent/analysis_loop.py`
- Create: `backend/src/customer_signal/agent/generic_fixture.py`
- Create: `backend/tests/test_analysis_loop.py`
- Modify: `backend/src/customer_signal/agent/contracts.py`
- Modify: `backend/src/customer_signal/agent/report_composer.py`
- Modify: `backend/src/customer_signal/agent/fixture.py`
- Modify: `backend/tests/test_fixture_runner.py`

- [ ] **Step 1: Model protocol과 세 가지 계획 RED 테스트 작성**

```python
@pytest.mark.parametrize(
    ("question", "expected_primitive", "metric_key", "value"),
    [
        (NEGATIVE_TOPIC_QUESTION, "aggregate_events", "negative_feedback_customer_count", 6),
        (REPEAT_VOC_QUESTION, "match_sequence", "matched_customer_count", 6),
        (SIGNUP_ABANDONMENT_QUESTION, "match_sequence", "abandoned_customer_count", 5),
    ],
)
async def test_loop_executes_distinct_fact_backed_plans(question, expected_primitive, metric_key, value):
    outcome = await loop.run(request_for(question), emit=events.append)
    assert expected_primitive in [fact.primitive for fact in outcome.facts]
    assert outcome.report.metric(metric_key).value == value
    assert all(note.fact_ids for note in outcome.notes)
```

PII/raw export/쓰기/지원하지 않는 통계 질문은 Tool과 Fact 없이 `unsupported_analysis`로 종료하고
안전한 이유와 추천 질문을 남겨야 합니다. 선택 Source/기간에 Event가 없으면 결론을 만들지 않고
`degraded` 상태, 빈 Fact/Note, 명시적 limitation을 반환해야 합니다.

- [ ] **Step 2: Analysis Loop RED 확인**

Run: `uv run --project backend pytest backend/tests/test_analysis_loop.py -q`

Expected: import failure for `customer_signal.agent.analysis_loop`

- [ ] **Step 3: AnalysisModel protocol과 loop 구현**

```python
class AnalysisModel(Protocol):
    async def create_goal(self, request: RunRequest, manifests: list[SourceManifest]) -> GoalDecision: ...
    async def create_plan(self, goal: AnalysisGoal, manifests: list[SourceManifest]) -> AnalysisPlan: ...
    async def create_note(self, context: StepModelContext) -> AnalysisNoteDraft: ...
    async def select_next(self, context: SelectionContext) -> StepSelection: ...
    async def create_report(self, context: ReportModelContext) -> CustomerSignalReportDraft: ...

class StepModelContext(DomainModel):
    goal: AnalysisGoal
    plan: AnalysisPlan
    step: AnalysisStep
    facts: list[AnalysisFact]
    current_fact: AnalysisFact

class SelectionContext(DomainModel):
    goal: AnalysisGoal
    plan: AnalysisPlan
    completed_step_ids: frozenset[str]
    facts: list[AnalysisFact]

class ReportModelContext(DomainModel):
    goal: AnalysisGoal
    plan: AnalysisPlan
    facts: list[AnalysisFact]
    notes: list[AnalysisNote]

class LegacyRunnerOutcome(DomainModel):
    outcome_kind: Literal["legacy"] = "legacy"
    status: RunStatus
    report: InsightReport | None = None
    facts: RunFacts
    agent_mode: Literal["fixture", "gemini"]

class GenericRunnerOutcome(DomainModel):
    outcome_kind: Literal["generic"] = "generic"
    status: RunStatus
    goal: AnalysisGoal | None = None
    clarification: ClarificationRequired | None = None
    unsupported: UnsupportedAnalysis | None = None
    plan: AnalysisPlan | None = None
    facts: list[AnalysisFact] = Field(default_factory=list)
    notes: list[AnalysisNote] = Field(default_factory=list)
    report: CustomerSignalReport | None = None
    limitations: list[str] = Field(default_factory=list)
    error: PublicRunError | None = None
    agent_mode: Literal["fixture", "gemini"]
    model: str | None = None

RunnerOutcome = Annotated[
    LegacyRunnerOutcome | GenericRunnerOutcome,
    Field(discriminator="outcome_kind"),
]

# Outcome validation binds status to clarification, unsupported, limitation and report presence.

class AnalysisLoop:
    async def run(self, request: RunRequest, emit: EventEmitter) -> GenericRunnerOutcome:
        decision = await self._model.create_goal(request, self._registry.manifests(request.enabled_sources))
        if isinstance(decision, ClarificationRequired):
            return GenericRunnerOutcome.awaiting_clarification(decision)
        if isinstance(decision, UnsupportedAnalysis):
            return GenericRunnerOutcome.failed_unsupported(decision)
        validate_goal_against_request(decision, request)
        scope = EventScope.from_validated_goal(
            decision, request=request, max_events=self._limits.max_events,
        )
        plan = self._validated_plan(decision)
        return await self._execute_plan(request, decision, plan, scope, emit)
```

Outcome validator는 `awaiting_clarification`일 때 clarification만, unsupported failure일 때
`unsupported`와 safe error/suggested questions를 요구합니다. no-data `degraded`는 `report is None`,
empty Facts/Notes와 non-empty server limitation을 exact 요구합니다. 같은 규칙을 Outcome, Artifact,
SSE golden과 UI reducer가 사용합니다. 이 필드는 Coordinator의 SSE error, Artifact와 UI까지 손실 없이 전달합니다.
`EventScope.from_validated_goal()`만 실행 Scope를 만들 수 있습니다. 공개 Goal의 기간/Source와 실제
Primitive Fact scope가 같아야 하며 hard `max_events`는 server settings에서만 주입합니다.
`AnalysisRunner.run()`은 위 `RunnerOutcome` union을 반환합니다. 기존 Fixture/Gemini runner는
`LegacyRunnerOutcome`을 만들어 기존 `RunFacts` authorization을 유지하고 generic loop만 ordered
`AnalysisFact` ledger를 사용합니다.
각 Step은 `step_started`, legacy `tool_started`, `fact_created`, 검증된 `analysis_note_created`,
`step_completed`, legacy `tool_completed` 순서로 checkpoint합니다.
Fact가 생성된 뒤 Note 검증에 실패하면 Fact와 오류는 보존하고 Note와 result는 공개하지 않습니다.
Step 실행은 반드시 `await executor.execute_async(..., budget=run_budget)`를 사용하고, 정상 반환된 Fact만
`fact_created`와 Artifact checkpoint에 전달합니다.
Fact 검증 직후 서버가 `stop_condition`을 먼저 평가합니다. `StopOnEmpty`는
`processing.scanned_events > 0 and returned_rows == 0`, `StopOnMetric`은 exact `AnalysisMetricFact` 비교로
정의합니다. Source/기간 자체가 비어 `scanned_events == 0`이면 Executor가 Fact build/publication 전에
typed `NoDataScope`를 반환하고 loop가 empty Fact/Note의 `degraded` limitation으로 처리합니다.
반면 Event는 있지만 match가 0인 `StopOnEmpty`는 검증 가능한 zero Fact를 보존하고 model `select_next`만
건너뜁니다. Metric stop도 현재 Fact의 검증 Note까지 만든 뒤 selection 없이 보고서 단계로 갑니다.
조건이 false일 때만 model `StepSelection`을 요청합니다.

- [ ] **Step 4: revision, clarification, unsupported와 partial failure RED 테스트 작성 및 확인**

완료 Step 변경 revision, 7번째 Step, unknown next Step을 fail closed합니다.
`ClarificationRequired`는 Tool을 호출하지 않고 same-run resume token을 반환합니다.
Primitive와 Note 실패는 완료 Fact까지만 outcome에 남깁니다.

Report draft가 다른 Run Fact, 존재하지 않는 Claim, action 또는 Evidence를 참조하면 결과를 공개하지
않습니다. 빈 Source/기간은 `degraded`로 끝나고 결론/추천 action 없이 limitation만 남깁니다.
Server `StopOnEmpty`/`StopOnMetric`이 true일 때 `select_next` 호출이 0회인지, false일 때만 strict
StepSelection union을 받는지 테스트합니다. 동시에 stop/next/revision field를 넣은 model payload는
structured validation에서 거부합니다.
별도 RED로 `scanned_events=0`은 Fact/Note/event 없이 degraded인지, `scanned_events>0`/`returned_rows=0`은
zero Fact를 보존하고 completed zero-result 보고서로 갈 수 있는지 고정합니다.

Run: `uv run --project backend pytest backend/tests/test_analysis_loop.py -q`

Expected: FAIL on revision, unsupported, no-data and forged report paths that are not implemented yet.

- [ ] **Step 5: GenericFixtureModel과 범용 report composer 구현**

Fixture는 세 수용 질문과 명시적 모호 질문만 구조화합니다. 질문 결과를 직접 만들지 않고 Production과 같은 Plan, Executor, Note validator와 report composer를 사용합니다.

```python
def compose_customer_signal_report(goal: AnalysisGoal, facts: Sequence[AnalysisFact],
                                   notes: Sequence[AnalysisNote],
                                   draft: CustomerSignalReportDraft) -> CustomerSignalReport:
    validate_report_draft(draft, goal=goal, facts=facts, notes=notes)
    return CustomerSignalReport(
        goal=goal,
        headline=render_headline(goal, facts),
        metrics=collect_metrics(facts),
        signals=collect_signals(facts),
        ranked_customers=collect_rankings(facts),
        representative_journeys=collect_journeys(facts),
        findings=collect_verified_findings(notes, selected_claim_ids=draft.claim_refs),
        recommendations=render_verified_actions(draft.recommended_actions, facts, notes),
        limitations=collect_limitations(notes),
        provenance=build_report_provenance(facts),
    )
```

`RecommendedActionDraft`는 `claim_refs`와 `fact_refs`를 필수로 가지며, 공개 Finding과 Recommendation은
같은 Run의 검증된 Note/Fact에 exact binding된 서버 문장만 사용합니다. Model prose를 그대로 복사하지 않습니다.
`draft.claim_refs`는 검증된 Note의 stable `claim_id` subset이어야 하며 중복, 누락, 다른 Run 참조를
거부합니다. 공개 Finding은 선택된 Claim만 렌더링합니다. 보고서 limitation은 draft 자유 문장이 아니라
검증된 Note와 no-data branch의 server-owned limitation만 사용합니다.

- [ ] **Step 6: Task 6 GREEN과 legacy Fixture 회귀 확인**

Run: `uv run --project backend pytest backend/tests/test_analysis_loop.py backend/tests/test_fixture_runner.py backend/tests/test_validator.py -q`

Expected: PASS

- [ ] **Step 7: Task 6 커밋**

```bash
git add backend/src/customer_signal/agent backend/tests/test_analysis_loop.py backend/tests/test_fixture_runner.py
git commit -m "feat: (agent) 검증형 범용 Analysis Loop 추가"
```

### Task 7: Gemini 구조화 Goal, Plan, Note와 보고서 단계

**Files:**
- Create: `backend/src/customer_signal/agent/generic_gemini.py`
- Create: `backend/tests/test_generic_gemini.py`
- Modify: `backend/src/customer_signal/agent/gemini.py`
- Modify: `backend/src/customer_signal/config.py`
- Modify: `backend/tests/test_gemini_adapter.py`
- Modify: `backend/tests/test_config.py`

- [ ] **Step 1: staged structured model RED 테스트 작성**

```python
async def test_gemini_model_uses_manifest_goal_plan_note_report_stages() -> None:
    model = GeminiAnalysisModel(model_factory=fake_model_factory(STAGED_RESPONSES))
    outcome = await AnalysisLoop(model=model, executor=EXECUTOR, validators=VALIDATORS).run(REQUEST, emit=EVENTS.append)
    assert [call.schema for call in RECORDED_CALLS] == [
        GoalDecision, AnalysisPlan, AnalysisNoteDraft, StepSelection,
        AnalysisNoteDraft, StepSelection, CustomerSignalReportDraft,
    ]
    assert outcome.report.provenance.result_ids == exact_result_ids(outcome.facts)
```

실제 단계 수에 따라 Note와 Selection 호출 수가 늘어날 수 있으므로 테스트 helper는 Plan Step 수로 기대 목록을 계산합니다.

- [ ] **Step 2: Gemini staged model RED 확인**

Run: `uv run --project backend pytest backend/tests/test_generic_gemini.py -q`

Expected: import failure for `customer_signal.agent.generic_gemini`

- [ ] **Step 3: GeminiAnalysisModel happy path 구현**

각 호출은 `with_structured_output()`으로 strict Pydantic 타입을 요청합니다.
Prompt에는 질문, 허용 Manifest와 Primitive schema, 현재 검증 Fact만 넣습니다.
Raw Evidence, 다른 Run, Shell, SQL, 파일과 비공개 reasoning은 넣지 않습니다.

```python
class GeminiAnalysisModel(AnalysisModel):
    async def create_plan(self, goal: AnalysisGoal, manifests: list[SourceManifest]) -> AnalysisPlan:
        chain = self._model().with_structured_output(AnalysisPlan)
        return await chain.ainvoke(build_plan_prompt(goal, manifests, max_steps=6))
```

모든 단계는 40초 request timeout과 전체 Run timeout 안에서 실행합니다.
`gemini-3.7-flash`가 첫 구조화 호출 전에 typed `NOT_FOUND`를 반환할 때만
`gemini-3.6-flash`로 재시도하고 Artifact에 실제 모델을 기록합니다.

- [ ] **Step 4: Provider 실패와 조작 출력 RED 테스트 작성 및 확인**

모델 timeout, malformed plan, unknown Source, 같은 숫자 다른 의미 Claim, fabricated customer/evidence,
completed Step revision과 cancellation을 각각 테스트합니다.
Generic mode는 어떤 실패에서도 legacy Fixture 결과를 반환하면 안 됩니다.

Run: `uv run --project backend pytest backend/tests/test_generic_gemini.py -q`

Expected: FAIL on timeout, fallback boundary, malformed output and cancellation cases not yet hardened.

- [ ] **Step 5: timeout, typed fallback와 조작 출력 fail-closed 구현**

각 structured call에 request timeout을 적용하고 외부 cancellation을 삼키지 않습니다.
첫 호출 전 typed model-not-found 외에는 모델 fallback을 하지 않으며 generic provider 오류를 legacy
Fixture로 바꾸지 않습니다. 모든 model draft는 Task 2 validator를 통과한 뒤에만 loop로 반환합니다.

- [ ] **Step 6: Task 7 GREEN과 기존 Gemini 보안 회귀 확인**

Run: `uv run --project backend pytest backend/tests/test_generic_gemini.py backend/tests/test_gemini_adapter.py backend/tests/test_config.py -q`

Expected: PASS

- [ ] **Step 7: Task 7 커밋**

```bash
git add backend/src/customer_signal/agent/generic_gemini.py \
  backend/src/customer_signal/agent/gemini.py \
  backend/src/customer_signal/config.py \
  backend/tests/test_generic_gemini.py \
  backend/tests/test_gemini_adapter.py \
  backend/tests/test_config.py
git commit -m "feat: (agent) Gemini 범용 분석 단계 모델 추가"
```

### Task 8: Run lifecycle, clarification과 Artifact API

**Files:**
- Modify: `backend/src/customer_signal/runtime/events.py`
- Modify: `backend/src/customer_signal/runtime/run_store.py`
- Modify: `backend/src/customer_signal/runtime/coordinator.py`
- Modify: `backend/src/customer_signal/api.py`
- Modify: `backend/tests/test_api.py`
- Create: `backend/tests/test_runtime_generic.py`
- Create: `contracts/generic-run-events.json`

- [ ] **Step 1: golden event와 snapshot 계약 RED 테스트 작성**

```python
async def test_generic_run_publishes_contiguous_events_and_persists_matching_cursor(client) -> None:
    accepted = (await client.post("/api/runs", json=GENERIC_REQUEST)).json()
    events = await read_sse(client, accepted["events_url"])
    assert [event["id"] for event in events] == list(range(1, len(events) + 1))
    assert "goal_created" in event_types(events)
    assert "analysis_note_created" in event_types(events)
    artifact = (await client.get(f"/api/run-artifacts/{accepted['run_id']}")).json()
    assert artifact["last_event_id"] == events[-1]["id"]
```

Golden fixture는 저장소 루트 `contracts/generic-run-events.json` 하나만 두고 Backend와 Frontend가
같은 파일을 직접 읽습니다. 복사본이나 언어별 fixture를 만들지 않습니다. Event payload 계약은 다음과 같습니다.

| Event | Payload |
| --- | --- |
| `run_started` | `{status}` |
| `goal_created` | `{goal}` |
| `clarification_required` | `{clarification_id, question}` |
| `plan_created`, `plan_revised` | `{plan}` |
| `step_started` | `{step_id, primitive, started_at}` |
| `fact_created` | `{step_id, fact}` |
| `analysis_note_created` | `{note}` |
| `step_completed` | `{step_id, status, result_ids, duration_ms}` |
| `report_validating` | `{result_ids}` |
| `result` | `{agent_mode, report}` |
| `error` | `{code, message, step_id?, suggested_questions?}` |
| `done` | `{status, limitations?}` |

각 payload는 strict Pydantic 모델이며 `RunEvent` discriminated union으로 생성합니다. Legacy event는
별도 union variant로 계속 decode하지만 golden generic lifecycle에는 중복 legacy tool 이벤트도 명시합니다.
Generic `done(degraded)`는 non-empty server-owned `limitations`를 필수로 포함하고 다른 terminal status는
빈 값만 허용합니다. shared golden에는 completed lifecycle과 degraded no-data lifecycle을 모두 둡니다.
Backend 테스트는 no-data 실행의 마지막 SSE와 Artifact가 같은 limitation을 exact 보존하는지 확인합니다.

- [ ] **Step 2: clarification lifecycle RED 테스트 작성**

```python
async def test_clarification_resumes_same_run_without_done(client) -> None:
    accepted = await create_run(client, AMBIGUOUS_REQUEST)
    first_events = await events_until(client, accepted.run_id, "clarification_required")
    assert "done" not in event_types(first_events)
    resumed = await client.post(f"/api/runs/{accepted.run_id}/clarification", json={"answer": "최근 30일"})
    assert resumed.status_code == 202
    assert resumed.json()["run_id"] == accepted.run_id
    assert (await wait_snapshot(client, accepted.run_id))["status"] == "completed"
```

Unknown Run 404, wrong status 409, blank answer 422와 실행 중 process를 잡고 있지 않는 상태를 검증합니다.

Run: `uv run --project backend pytest backend/tests/test_runtime_generic.py -q`

Expected: FAIL because generic statuses, typed events and clarification resume do not exist.

- [ ] **Step 3: RunStore와 events 확장 구현**

Task 2의 shared `RunStatus`를 사용해 `awaiting_clarification`과 `degraded`를 모두 추가합니다.
Snapshot에는 request, goal, plan, facts, notes, generic/legacy report와 `last_event_id`를 둡니다.
새 event payload는 typed 모델로 생성하고 recursive public-data validator를 통과해야 합니다.

- [ ] **Step 4: Coordinator checkpoint persistence와 no-fallback 구현**

Run 생성, clarification, Goal/Plan, 각 Step, revision, 성공/실패 terminal에서 Artifact를 저장합니다.
Artifact 쓰기 실패는 `artifact_write_failed` 오류와 `done(failed)`를 남기며 이후 result를 공개하지 않습니다.
`auto` generic Provider 실패는 Fixture로 전환하지 않습니다.
Coordinator는 전체 generic 실행을 `asyncio.timeout(settings.run_timeout_seconds)`로 감싸고 timeout,
shutdown, 외부 cancellation에서 `RunBudget.cancel()`을 호출합니다. 느린 fake Primitive의 Fact가 timeout 뒤
공개되지 않고 `error -> done(failed)`로 끝나는 테스트와 app shutdown cancellation 테스트를 추가합니다.

`RunAuthorization`은 `from_legacy_facts(RunFacts)`와 `from_analysis_facts(list[AnalysisFact])` 두 생성자를
가집니다. generic 실행 중에는 이미 publish된 Fact의 customer/evidence ID만 상세 조회할 수 있고,
미공개 ID는 404입니다. 재시작 뒤에는 Artifact Fact로 동일 allowlist를 복원합니다. terminal 상태가 아니어도
publish된 Fact가 권한을 부여한 Journey/Evidence는 조회 가능하되 아직 Fact에 없는 ID는 허용하지 않습니다.

- [ ] **Step 5: Artifact와 download API RED 테스트 작성**

목록 최신순, unknown UUID 404, unsupported version 409와 JSON/Markdown `Content-Disposition`을 테스트합니다.
문서가 Artifact 밖 값을 추가하지 않는지 확인합니다.
재시작 뒤 완료/실패 Run 상세과 Journey/Evidence authorization도 복원해야 합니다.
`GET /api/sources`가 Registry의 공개 Manifest만 반환하고 PII 필드 이름과 원천 컬럼을 숨기는지도 검증합니다.
응답 타입은 Task 1의 `PublicSourceList`이며 `PublicSourceManifest.from_internal()` 결과만 serialize합니다.
테스트는 exact top-level/descriptor key set과 non-PII field만 남는지 검사합니다.
등록된 `test_adapter` 같은 built-in 외 동적 Source ID가 `/api/sources`에 나타나고 같은 ID를 넣은
`POST /api/runs`가 `RunRequest` validation부터 Registry lookup까지 통과하는 API 테스트를 추가합니다.
Legacy/Generic outcome union, generic report에 Journey가 없는 경우 자동 상세 조회가 없다는 runtime 테스트도 추가합니다.

Run: `uv run --project backend pytest backend/tests/test_runtime_generic.py backend/tests/test_api.py -q`

Expected: FAIL because artifact routes, restored authorization and dynamic source wiring are absent.

- [ ] **Step 6: FastAPI route와 dependency wiring 구현**

```python
@router.post("/api/runs/{run_id}/clarification", status_code=202)
async def submit_clarification(run_id: UUID, body: ClarificationAnswer) -> RunAccepted: ...

@router.get("/api/sources")
async def list_sources() -> PublicSourceList: ...

@router.get("/api/run-artifacts")
async def list_artifacts() -> ArtifactListResponse: ...

@router.get("/api/run-artifacts/{run_id}")
async def get_artifact(run_id: UUID) -> RunArtifact: ...

@router.get("/api/run-artifacts/{run_id}/document")
async def get_document(run_id: UUID) -> ArtifactDocument: ...

@router.get("/api/run-artifacts/{run_id}/download.json")
async def download_json(run_id: UUID) -> Response: ...

@router.get("/api/run-artifacts/{run_id}/download.md")
async def download_markdown(run_id: UUID) -> Response:
    return Response(render_markdown(store.load(run_id)), media_type="text/markdown",
                    headers={"Content-Disposition": f'attachment; filename="{run_id}.md"'})
```

Store 생성은 import 시 디렉터리를 쓰지 않습니다. App startup에서 current DB 준비 뒤 dependency를 구성합니다.
각 checkpoint 뒤 `ArtifactStore.load(run_id)`를 즉시 확인하는 테스트로 생성, clarification, Goal/Plan,
Fact/Note, revision, terminal 상태의 `last_event_id`와 `updated_at`이 memory snapshot과 일치함을 고정합니다.

- [ ] **Step 7: Task 8 GREEN과 Backend 전체 회귀 확인**

Run: `uv run --project backend pytest backend/tests/test_runtime_generic.py backend/tests/test_api.py -q`

Run: `uv run --project backend pytest backend/tests -q`

Run: `uv run --project backend ruff check backend`

Expected: all tests PASS and Ruff `All checks passed!`

- [ ] **Step 8: Task 8 커밋**

```bash
git add contracts/generic-run-events.json backend/src/customer_signal/runtime backend/src/customer_signal/api.py backend/tests/test_runtime_generic.py backend/tests/test_api.py
git commit -m "feat: (api) 범용 Run 기록과 확인 질문 API 추가"
```

### Task 9: Frontend generic 계약, Client와 Reducer

**Files:**
- Create: `frontend/src/features/customer-intelligence/run-contract-decoders.ts`
- Create: `frontend/src/features/customer-intelligence/source-catalog.ts`
- Create: `frontend/src/features/customer-intelligence/run-selectors.ts`
- Create: `frontend/src/features/customer-intelligence/__tests__/run-contract-decoders.test.ts`
- Modify: `frontend/src/features/customer-intelligence/contracts.ts`
- Modify: `frontend/src/features/customer-intelligence/run-client.ts`
- Modify: `frontend/src/features/customer-intelligence/run-reducer.ts`
- Modify: `frontend/src/features/customer-intelligence/__tests__/run-client.test.ts`
- Modify: `frontend/src/features/customer-intelligence/__tests__/run-reducer.test.ts`

- [ ] **Step 1: Next.js와 React 관련 로컬 가이드 확인**

Run: `sed -n '1,220p' frontend/AGENTS.md`

Run: `rg -n "use client|Client Component" frontend/node_modules/next/dist/docs -g '*.md' | head -20`

Expected: Next 16 generated instructions and relevant Client Component guide locations.

- [ ] **Step 2: golden generic event decoder RED 테스트 작성**

```typescript
it("decodes the backend golden goal-plan-fact-note lifecycle", () => {
  const golden = JSON.parse(readFileSync(resolveRepo("contracts/generic-run-events.json"), "utf8"));
  const events = golden.events.map((event: unknown) => decodeRunEvent(event));
  expect(events.map((event) => event.type)).toContain("analysis_note_created");
  expect(events.at(-1)).toMatchObject({ type: "done", payload: { status: "completed" } });
});

it("rejects a note that references an absent fact", () => {
  expect(() => decodeRunEvent(noteWithUnknownFact)).toThrow(/fact_refs/);
});
```

Legacy 8개 이벤트와 `InsightReport` fixture는 계속 decode해야 합니다.
Backend와 Frontend 테스트가 모두 같은 root golden 파일의 event 수, discriminators와 payload key를
검사해 어느 한쪽 schema 변경이 parity failure로 드러나게 합니다.

- [ ] **Step 3: Decoder RED 확인**

Run: `npm --prefix frontend test -- --run src/features/customer-intelligence/__tests__/run-contract-decoders.test.ts`

Expected: import failure for `run-contract-decoders`

- [ ] **Step 4: generic/legacy discriminated 계약과 decoder 구현**

`SourceId`는 non-empty manifest string으로 바꾸고 알려진 5개 값만 `sourceLabel()`에서 표시 이름을 가집니다.
Generic report와 legacy `InsightReport`를 `report_kind` discriminated union으로 분리합니다.
`parse-sse.ts`는 수정하지 않습니다.

- [ ] **Step 5: Client API RED 테스트 작성**

```typescript
it("submits clarification and loads persisted artifacts", async () => {
  await client.submitClarification("run-1", "최근 30일");
  await client.listSources();
  await client.listRunArtifacts();
  await client.getRunArtifact("run-1");
  await client.getRunDocument("run-1");
  expect(fetchMock.calls.map(([url]) => String(url))).toEqual([
    `${base}/api/runs/run-1/clarification`, `${base}/api/sources`, `${base}/api/run-artifacts`,
    `${base}/api/run-artifacts/run-1`, `${base}/api/run-artifacts/run-1/document`,
  ]);
});
```

Download URL encoding, Last-Event-ID, dynamic Source, malformed Artifact와 failed response body cancellation도 테스트합니다.

- [ ] **Step 6: Client API RED 확인**

Run: `npm --prefix frontend test -- --run src/features/customer-intelligence/__tests__/run-client.test.ts`

Expected: FAIL because source, artifact, document and clarification client methods are absent.

- [ ] **Step 7: Client와 boundary decoder 구현**

`run-client.ts`는 fetch와 SSE transport만 유지합니다.
모든 JSON 검증은 `run-contract-decoders.ts`에 위임합니다.
`listSources`, `listRunArtifacts`, `getRunArtifact`, `getRunDocument`, `submitClarification`,
`jsonDownloadUrl`, `markdownDownloadUrl`을 추가합니다.

- [ ] **Step 8: Reducer lifecycle RED 테스트 작성**

Goal → Plan → Step → Fact → Note → generic result를 테스트합니다.
Same-run clarification, revision의 완료 Step 보존, partial failure, Artifact hydration과 cursor dedupe도 검증합니다.
Mixed legacy/new stream은 화면에서 중복 표시하지 않아야 합니다.
`unsupported_analysis`는 safe message와 추천 질문을 보존해야 합니다. `done(degraded)`는 report가 없어도
protocol error로 바꾸지 않고 server-owned limitation을 보존해야 합니다.

- [ ] **Step 9: Reducer RED 확인**

Run: `npm --prefix frontend test -- --run src/features/customer-intelligence/__tests__/run-reducer.test.ts`

Expected: FAIL because generic lifecycle, hydration and clarification actions are absent.

- [ ] **Step 10: Reducer와 selectors 구현**

```typescript
export interface GenericRunState {
  phase: RunPhase;
  goal: AnalysisGoal | null;
  clarification: Clarification | null;
  unsupported: UnsupportedAnalysis | null;
  error: PublicRunError | null;
  limitations: string[];
  plan: AnalysisPlan | null;
  facts: Record<string, AnalysisFact>;
  notes: AnalysisNote[];
  report: CustomerSignalReport | InsightReport | null;
  lastEventId: number;
}
```

`hydrate_artifact`는 Artifact의 `last_event_id`까지 상태를 복원합니다. 새 Step 이벤트가 하나라도 있으면 legacy tool 이벤트를 내부에는 보존하되 화면 selector에서는 숨깁니다.

- [ ] **Step 11: Task 9 GREEN과 typecheck 확인**

Run:

```bash
npm --prefix frontend test -- --run \
  src/features/customer-intelligence/__tests__/run-contract-decoders.test.ts \
  src/features/customer-intelligence/__tests__/run-client.test.ts \
  src/features/customer-intelligence/__tests__/run-reducer.test.ts
```

Run: `npm --prefix frontend run typecheck`

Expected: PASS

- [ ] **Step 12: Task 9 커밋**

```bash
git add frontend/src/features/customer-intelligence/contracts.ts \
  frontend/src/features/customer-intelligence/run-contract-decoders.ts \
  frontend/src/features/customer-intelligence/source-catalog.ts \
  frontend/src/features/customer-intelligence/run-selectors.ts \
  frontend/src/features/customer-intelligence/run-client.ts \
  frontend/src/features/customer-intelligence/run-reducer.ts \
  frontend/src/features/customer-intelligence/__tests__
git commit -m "feat: (frontend) 범용 Run 계약과 기록 상태 추가"
```

### Task 10: Chat, Analysis Workspace와 문서 기록 UI

**Files:**
- Create: `frontend/src/features/customer-intelligence/ChatPanel.tsx`
- Create: `frontend/src/features/customer-intelligence/RunHistory.tsx`
- Create: `frontend/src/features/customer-intelligence/AnalysisWorkspace.tsx`
- Create: `frontend/src/features/customer-intelligence/AnalysisGoalCard.tsx`
- Create: `frontend/src/features/customer-intelligence/AnalysisPlanView.tsx`
- Create: `frontend/src/features/customer-intelligence/AnalysisNoteTimeline.tsx`
- Create: `frontend/src/features/customer-intelligence/FactDetail.tsx`
- Create: `frontend/src/features/customer-intelligence/RunDocumentView.tsx`
- Create: `frontend/src/features/customer-intelligence/RunDownloads.tsx`
- Create: `frontend/src/features/customer-intelligence/LegacyInsightWorkspace.tsx`
- Create: `frontend/src/features/customer-intelligence/__tests__/ChatPanel.test.tsx`
- Create: `frontend/src/features/customer-intelligence/__tests__/RunHistory.test.tsx`
- Create: `frontend/src/features/customer-intelligence/__tests__/AnalysisWorkspace.test.tsx`
- Modify: `frontend/src/features/customer-intelligence/CustomerIntelligencePage.tsx`
- Modify: `frontend/src/features/customer-intelligence/QueryComposer.tsx`
- Modify: `frontend/src/features/customer-intelligence/AgentTrace.tsx`
- Modify: `frontend/src/features/customer-intelligence/EvidenceDrawer.tsx`
- Modify: `frontend/src/features/customer-intelligence/use-run-controller.ts`
- Modify: `frontend/src/features/customer-intelligence/__tests__/CustomerIntelligencePage.test.tsx`
- Modify: `frontend/src/features/customer-intelligence/__tests__/QueryComposer.test.tsx`
- Modify: `frontend/src/app/globals.css`
- Modify: `frontend/src/app/layout.tsx`

- [ ] **Step 1: Chat과 clarification RED 테스트 작성**

```tsx
it("keeps clarification in the same run and focuses the answer", async () => {
  render(<CustomerIntelligencePage client={client} />);
  await user.type(screen.getByRole("textbox", { name: "분석 질문" }), "최근 고객 신호를 알려줘");
  await user.click(screen.getByRole("button", { name: "분석 시작" }));
  client.emit(clarificationRequiredEvent);
  expect(screen.getByRole("textbox", { name: "확인 답변" })).toHaveFocus();
  await user.type(screen.getByRole("textbox", { name: "확인 답변" }), "최근 30일 부정 피드백");
  await user.click(screen.getByRole("button", { name: "답변하고 계속" }));
  expect(client.submitClarification).toHaveBeenCalledWith(RUN_ID, "최근 30일 부정 피드백");
});
```

세 추천 질문, 동적 Source 토글, `role=log`, blank validation과 abort race를 테스트합니다.
Unsupported 응답은 safe reason과 서버 추천 질문 버튼을 표시하고, PII나 Provider 원문을 표시하지 않아야 합니다.

- [ ] **Step 2: Run History와 reload RED 테스트 작성**

Mount 목록 조회, 완료/실패/진행 Artifact 선택, stale response 무시, `aria-current`, Artifact hydration 후 Last-Event-ID resume를 테스트합니다.

- [ ] **Step 3: Analysis Workspace RED 테스트 작성**

```tsx
it("renders verified notes and document downloads on the right", async () => {
  render(<AnalysisWorkspace state={genericCompletedState} document={documentFixture} />);
  expect(screen.getByText("검색 실패 후보 24명")).toBeVisible();
  expect(screen.getByText("VOC까지 이어진 고객 6명")).toBeVisible();
  expect(screen.getByRole("link", { name: "JSON 다운로드" })).toHaveAttribute("href", expect.stringContaining("download.json"));
  expect(screen.getByRole("link", { name: "Markdown 다운로드" })).toHaveAttribute("href", expect.stringContaining("download.md"));
});
```

Fact disclosure, result/evidence ID, partial failure, generic report without Ranking/Journey와 legacy report 분기를 테스트합니다.
No-data `done(degraded)`는 보고서 없음 오류 대신 limitation card를 표시하고 JSON/Markdown 기록 링크를 유지합니다.

- [ ] **Step 4: UI flow RED 확인**

Run:

```bash
npm --prefix frontend test -- --run \
  src/features/customer-intelligence/__tests__/ChatPanel.test.tsx \
  src/features/customer-intelligence/__tests__/RunHistory.test.tsx \
  src/features/customer-intelligence/__tests__/AnalysisWorkspace.test.tsx \
  src/features/customer-intelligence/__tests__/CustomerIntelligencePage.test.tsx
```

Expected: FAIL because history, clarification and generic workspace components are absent.

- [ ] **Step 5: Controller history, resume와 clarification 구현**

기존 abort/version guard를 모든 새 request에 적용합니다.
`startOrResumeStream(runId, lastEventId)`를 한 경로로 만듭니다.
Mount Source/History load, Artifact 선택, document load, clarification submit 뒤 같은 Run resume와 terminal history refresh를 구현합니다.

- [ ] **Step 6: Chat과 오른쪽 Workspace 컴포넌트 구현**

`CustomerIntelligencePage` DOM 순서는 Chat 다음 Workspace입니다.
기존 Summary, Ranking, Journey는 `LegacyInsightWorkspace`로 감쌉니다.
Generic report에서는 자동 첫 고객 Journey 요청을 하지 않습니다.
Evidence는 Fact가 공개된 시점부터 해당 Run allowlist 안에서 열 수 있습니다.
Query Composer의 Source 선택지는 `GET /api/sources` 응답을 사용하며 Source ID를 코드 allowlist로 제한하지 않습니다.

- [ ] **Step 7: 반응형 CSS와 접근성 구현**

`>=1024px`에서 `minmax(320px, 400px) minmax(0, 1fr)`, 그 아래는 1열입니다.
720px 아래에서 Plan, Fact, Download action을 세로로 쌓습니다.
44px touch target, body horizontal overflow 없음, Evidence drawer full width를 유지합니다.
기존 `.result-workspace { grid-column: 1 }` 모바일 겹침 방지는 삭제하지 않습니다.

- [ ] **Step 8: Task 10 GREEN과 기존 UI 회귀 확인**

Run: `npm --prefix frontend test -- --run`

Run: `npm --prefix frontend run typecheck`

Run: `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000 npm --prefix frontend run build`

Expected: all tests PASS, TypeScript PASS, Next build PASS.

- [ ] **Step 9: Task 10 커밋**

```bash
git add frontend/src frontend/next-env.d.ts
git commit -m "feat: (frontend) 단계별 분석 노트와 Run 문서 화면 추가"
```

### Task 11: 범용 수용 E2E, 실제 Gemini와 문서 갱신

**Files:**
- Create: `frontend/e2e/generic-analysis.spec.ts`
- Modify: `frontend/playwright.config.ts`
- Modify: `README.md`
- Modify: `docs/verification/live-gemini-smoke.md`
- Modify: `Makefile`

- [ ] **Step 1: 세 질문 desktop/mobile 수용 E2E 작성**

```typescript
for (const scenario of scenarios) {
  test(`${scenario.name} produces distinct facts and a persisted document`, async ({ page }) => {
    await page.goto("/");
    await askQuestion(page, scenario.question);
    await expect(page.getByText(scenario.expectedPrimitive)).toBeVisible();
    await expect(page.getByText(scenario.expectedMetric)).toBeVisible();
    await page.reload();
    await openRunFromHistory(page, scenario.question);
    await expect(page.getByRole("heading", { name: scenario.expectedHeadline })).toBeVisible();
  });
}
```

두 viewport에서 세 질문, same-run clarification, JSON/Markdown 다운로드와 새로고침 복원을 검증합니다.
세로 DOM 순서와 body horizontal overflow 없음도 확인합니다.
기존 `working-demo.spec.ts`는 유지합니다.

- [ ] **Step 2: 첫 수용 E2E 실행과 통합 차이 기록**

Run: `AGENT_MODE=fixture npm --prefix frontend run e2e -- --project=desktop-chromium generic-analysis.spec.ts`

Expected: 이미 통합이 맞으면 PASS합니다. 실패하면 구현 누락인지 계약 drift인지 재현 근거를 남기고
테스트 기대값을 낮추지 않은 채 Step 3에서 수정합니다. 이 단계는 TDD RED가 아니라 수용 검증입니다.

- [ ] **Step 3: 통합 seam 수정과 E2E GREEN**

E2E에서 드러난 계약 차이는 Backend golden fixture를 기준으로 수정합니다. 테스트 selector를 구현 세부 DOM에 맞추지 않고 role, label과 공개 text에 맞춥니다.

Run: `AGENT_MODE=fixture npm --prefix frontend run e2e -- --project=desktop-chromium generic-analysis.spec.ts`

Run: `AGENT_MODE=fixture npm --prefix frontend run e2e -- --project=mobile-chromium generic-analysis.spec.ts`

Expected: all generic E2E PASS.

- [ ] **Step 4: 실제 Gemini smoke 실행**

기존 저장소 루트 `.env`를 Uvicorn `--env-file`로만 로드합니다.
Key를 출력하거나 Artifact에 저장하지 않습니다.
실제 `gemini-3.7-flash`로 세 질문 중 반복 행동 후 상담 전환 질문을 1회 실행합니다.

검증값:

- `agent_mode=gemini`
- Goal과 3~6단계 Plan 공개
- Primitive, Fact와 AnalysisNote 이벤트 존재
- 최종 Metric이 Fact와 exact 일치
- Artifact에 실제 모델, Manifest, Dataset version 기록
- Provider 원문, Secret, Raw PII 부재

- [ ] **Step 5: README와 검증 문서 갱신**

`README.md`에 범용 질문 3개, 오른쪽 분석 노트, Run 기록 경로와 download API를 추가합니다.
`live-gemini-smoke.md`에는 Run ID, 질문, 실제 Plan/Primitive sequence, Fact Metric,
Note와 Artifact 검증 결과만 남깁니다.

- [ ] **Step 6: 전체 검증**

Run: `make test`

Run: `make e2e`

Expected:

- Backend 전체 pytest PASS
- Ruff PASS
- Frontend Vitest PASS
- TypeScript PASS
- Next production build PASS
- legacy와 generic desktop/mobile E2E PASS

- [ ] **Step 7: 보안과 작업 트리 검사**

Run: `git diff --check`

Run: `git status --short`

Run: `rg -n "GEMINI_API_KEY|raw_fields|provider_response" data/run-artifacts docs/verification -g '*.json' -g '*.md'`

Expected: diff 오류 없음, 의도한 문서 변경만 존재, Secret/Raw PII/Provider 원문 match 없음.

- [ ] **Step 8: Task 11 커밋**

```bash
git add frontend/e2e frontend/playwright.config.ts README.md docs/verification/live-gemini-smoke.md Makefile
git commit -m "docs: (demo) 범용 고객 신호 분석 검증 경로 추가"
```

## 완료 조건

- 같은 Adapter 데이터에서 세 자연어 목표가 서로 다른 Goal, Plan, Fact, Note와 보고서를 생성합니다.
- SourceAdapter를 테스트용 In-memory 구현으로 바꿔도 동일한 Primitive 계약이 통과합니다.
- 오른쪽 Workspace는 각 단계의 목표, 검증 Fact, 공개 해석, 다음 단계와 한계를 실시간 표시합니다.
- 완료와 실패 Run은 서버 재시작과 페이지 새로고침 뒤에도 JSON Artifact에서 복원됩니다.
- JSON이 단일 원본이며 문서 View와 Markdown은 JSON 밖 사실을 추가하지 않습니다.
- 모델이 숫자, 단위, Source, 고객, Evidence 또는 의미를 바꾸면 공개 전에 차단됩니다.
- Generic Provider 실패는 legacy 6명 Fixture 결과로 대체되지 않습니다.
- 기존 Journey 데모와 API 호환 테스트가 유지됩니다.
- 실제 `gemini-3.7-flash`, 전체 테스트, desktop/mobile E2E가 통과합니다.

## 범위 중단 기준

- 세 수용 질문이 서로 다른 Fact와 보고서를 만들지 못하면 UI 확장 전에 Goal/Primitive 계약을 줄입니다.
- 범용 처리를 위해 자유 SQL이나 원천 컬럼 노출이 필요해지면 해당 질문 유형을 지원 범위에서 제외합니다.
- 실제 Gemini가 최대 6단계와 전체 Run timeout 안에서 완료하지 못하면 단계 수와 Primitive 조합을 줄입니다.
- Artifact가 검증된 Fact 밖 내용을 필요로 하면 문서 표현을 줄이고 별도 사실 저장소를 추가하지 않습니다.
