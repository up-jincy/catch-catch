# Analysis Pack, A2UI Frontend 구조 설계

작성일: 2026-08-27

상태: 설계 승인

## 결론

Backend의 append-only Canonical Run Event를 정본으로 사용합니다. 새 분석은
`AnalysisPackAdapter` Module 하나와 명시적 Registry 한 줄로 추가합니다. Frontend는 새 분석의
Goal, Plan, Primitive, Fact, Report 타입을 해석하지 않습니다.

A2UI는 Canonical Run Event에서 만드는 선택적 Presentation Projection입니다. Next.js Frontend는
Shell, trusted Catalog와 theme를 소유합니다. CopilotKit v2, AG-UI와 A2UI 의존성은
`CopilotKitV2HostAdapter`에 격리합니다.

이 구조는 다음 목표를 함께 만족해야 합니다.

- 새 분석 추가 시 Frontend 변경 제거
- Backend에 도메인 지식 집중
- Backend 재시작 뒤에도 가능한 cursor replay
- CopilotKit/A2UI 장애와 분석 성공 여부 분리
- theme와 Catalog renderer 교체를 통한 새 Frontend 디자인
- Fixture mode의 외부 provider 없는 결정론적 검증

## 배경과 현재 문제

현재 Backend는 Goal, Plan, Fact, Note, Report를 strict Pydantic 모델로 검증합니다. `AnalysisLoop`도
실행 순서와 공개 안전성 규칙을 소유합니다. 그러나 공개 stream이 상세 domain object를 그대로
전달하므로 Frontend가 같은 타입과 Primitive 어휘를 다시 선언하고 해석합니다.

새 Primitive 하나를 추가할 때 Backend의 이름, 입력 union, dependency arity, metric 규칙,
Planner 설명과 Frontend의 decoder, label, reducer를 함께 수정해야 합니다. 변경의 Locality가 낮고
누락 가능성이 큽니다.

현재 `RunStore`는 프로세스 안에서 연속 event ID와 `Last-Event-ID` 재연결을 제공합니다. 저장되는
Artifact는 event log가 아니라 snapshot입니다. 프로세스를 재시작하면 과거 event를 완전히 replay할
수 없습니다. 상태를 누적해서 그리는 A2UI surface에는 이 보장만으로 부족합니다.

기존 Frontend는 프로토타입이므로 새 구조에서는 재사용을 목표로 하지 않습니다. 새 Frontend가
승인된 E2E 계약을 통과할 때까지 비교 기준으로만 유지합니다.

## 설계 원칙

### Backend가 소유하는 지식

Backend는 다음 지식을 소유합니다.

- Analysis Pack 선택과 버전 고정
- Pack 입력, Goal, Plan, Fact, Report schema
- 분석 실행, Primitive 조합과 provider 호출
- Fact provenance와 Report claim 검증
- 공개 데이터 masking
- Run lifecycle, event ordering, idempotency와 replay
- Presentation Intent 생성

### Frontend가 소유하는 지식

Frontend는 다음 지식만 소유합니다.

- routing, layout와 접근성
- CopilotKit Host 연결
- trusted Catalog renderer와 prop schema
- theme token, typography와 motion
- 연결, loading와 오류 fallback

Frontend는 Pack ID별 switch나 분석 domain decoder를 만들지 않습니다.

### Dependency 방향

```text
React Shell
  -> AnalysisHost Interface
  -> CopilotKitV2HostAdapter
  -> CopilotKit/AG-UI/A2UI external runtime

Next Host
  -> Backend Run Gateway
  -> Pack Kernel
  -> AnalysisPackAdapter

Pack Kernel
  -> EventJournal
  -> SQLiteEventJournal / InMemoryEventJournal

Canonical Run Event
  -> Pack Projector
  -> PresentationIntent
  -> A2UIProjectionAdapter
  -> CopilotKit Host
```

다음 역방향 의존은 허용하지 않습니다.

- Analysis Pack의 React, CopilotKit, AG-UI 또는 A2UI wire type import
- Frontend의 Pack별 Goal, Plan, Fact, Report union 선언
- Analysis Pack의 EventJournal 직접 호출
- A2UI message의 Canonical Run 상태 변경

## Backend Module

### AnalysisPackAdapter

`AnalysisPackAdapter`는 한 분석의 domain 지식과 실행을 숨기는 Deep Module입니다. Pack 개발자가
구현하는 외부 Interface는 다음 형태입니다. 아래 코드는 구현 계획에서 구체화할 계약 스케치이며,
라이브러리의 실제 타입 이름을 미리 고정하지 않습니다.

```python
InputT = TypeVar("InputT", bound=BaseModel)


class ArtifactSchema(BaseModel):
    kind: Literal["goal", "plan", "fact", "report"]
    schema_id: str
    model: type[BaseModel]


class AnalysisPackSpec(BaseModel):
    pack_id: str
    pack_version: str
    title_ko: str
    description_ko: str
    input_schema_id: str
    artifact_schemas: tuple[ArtifactSchema, ...]
    required_catalog_keys: tuple[str, ...] = ()


class AnalysisPackAdapter(Protocol, Generic[InputT]):
    spec: AnalysisPackSpec
    Input: type[InputT]

    async def execute(
        self,
        request: InputT,
        context: PackContext,
    ) -> AsyncIterator[PackEmission]: ...

    def project(
        self,
        event: CanonicalRunEvent,
        state: PresentationState,
    ) -> Sequence[PresentationIntent]: ...
```

공통 Base Adapter가 generic `project()`를 제공합니다. Pack은 별도 표현이 필요할 때만 이를
override합니다.

Pack은 다음 `PackEmission`만 yield할 수 있습니다.

- `GoalDraft`
- `PlanDraft`
- `ActivityDraft`
- `FactDraft`
- `InteractionDraft`
- `ReportDraft`

Pack은 lifecycle status, sequence, event ID와 cursor를 만들지 않습니다. Pack Kernel이 emission을
검증하고 Canonical Run Event로 변환합니다.

### 명시적 Pack Registry

Pack은 자동 탐색하지 않습니다. composition root의 명시적 Registry에 등록합니다.

```python
PACKS = AnalysisPackRegistry(
    [
        CustomerSignalPack(dependencies),
        RepeatComplaintPack(dependencies),
    ]
)
```

Registry는 Backend 시작 시 다음 조건을 fail-fast로 검사합니다.

- `pack_id`와 `pack_version` 형식 및 중복
- strict Pydantic input과 Artifact schema
- `schema_id`와 schema digest 충돌
- JSON 직렬화 가능 여부
- `required_catalog_keys` 형식과 중복
- Pack 인스턴스 중복 등록

새 분석의 중앙 변경은 Pack Module과 Registry 한 줄입니다. 새 Pack이 기존 trusted Catalog key만
사용하면 Frontend를 수정하지 않습니다.

### Pack Kernel

Pack Kernel은 모든 Pack에 공통으로 필요한 실행 규칙을 숨깁니다.

- input과 emission schema 검증
- Goal, Plan, Fact, Report ordering과 cross-reference 검증
- timeout, cancellation과 budget
- 공개 오류 정규화
- PII와 금지 key 검사
- sequence와 event ID 부여
- EventJournal atomic append
- idempotency와 command state 검사
- terminal event 생성

Pack Kernel은 Pack이 yield한 batch 전체를 검증한 뒤 EventJournal에 저장합니다. 저장이 실패하면
외부 stream에 아무 event도 내보내지 않습니다.

### 기존 AnalysisLoop 재사용

현재 `AnalysisLoop`와 analytics Primitive는 첫 `CustomerSignalPack`의 Implementation으로
재사용합니다. 첫 전환에서 분석 알고리즘을 다시 작성하지 않습니다.

기존 Fixture와 Gemini Adapter도 `CustomerSignalPack` 내부에서 같은 Interface를 계속 사용합니다.
새 구조가 검증된 뒤 Legacy runner와 generic/legacy compatibility 분기를 제거합니다.

## Canonical Run Event

Canonical Run Event의 envelope는 Pack이 추가돼도 바뀌지 않아야 합니다.

```python
class PackRef(BaseModel):
    pack_id: str
    pack_version: str
    contract_digest: str


class VersionedValue(BaseModel):
    schema_id: str
    schema_digest: str
    value: JsonValue


class CanonicalRunEvent(BaseModel):
    schema_version: Literal[1]
    event_id: UUID
    run_id: UUID
    sequence: int
    occurred_at: AwareDatetime
    pack: PackRef
    kind: CoreEventKind
    artifact: VersionedValue | None = None
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    causation_id: UUID
    correlation_id: UUID
```

`CoreEventKind`는 다음 값으로 제한합니다.

```text
run.opened
artifact.committed
activity.changed
interaction.changed
run.awaiting_input
run.resumed
run.completed
run.degraded
run.failed
```

Pack별 Goal, Plan, Fact와 Report는 `artifact.committed`의 `VersionedValue`로 기록합니다. Frontend는
`value`의 domain 의미를 해석하지 않습니다. Backend Pack과 Projector만 해당 schema를 이해합니다.

### Event 불변조건

- Run별 `sequence` 값의 `1..N` 연속 증가
- `(run_id, sequence)`와 `event_id`의 고유성
- Run 시작 시 exact Pack version, contract digest와 projector version 고정
- Goal 이후 Plan, Plan 이후 Fact, Fact 이후 Report commit
- Artifact의 기존 event와 schema만 참조하는 cross-reference
- terminal event 1개와 terminal 이후 append 금지
- 동일 idempotency key와 command ID의 동일 receipt 반환
- provider 원문, 원본 PII, prompt와 private reasoning 저장 금지

정상 Run의 대표 순서는 다음과 같습니다.

```text
run.opened
artifact.committed(goal)
artifact.committed(plan)
activity.changed(started)
artifact.committed(fact) *
activity.changed(completed)
artifact.committed(report)
run.completed | run.degraded
```

사용자 입력이 필요하면 다음 순서를 삽입합니다.

```text
interaction.changed(requested)
run.awaiting_input
interaction.changed(answered)
run.resumed
```

## EventJournal과 Replay

EventJournal은 Canonical Run Event 저장과 cursor replay를 제공하는 Deep Module입니다.

```python
class EventJournal(Protocol):
    async def create(
        self,
        run_id: UUID,
        first: EventDraft,
        idempotency_key: str,
    ) -> StoredEvent: ...

    async def append(
        self,
        run_id: UUID,
        expected_sequence: int,
        drafts: Sequence[EventDraft],
    ) -> tuple[StoredEvent, ...]: ...

    async def read(
        self,
        run_id: UUID,
        after_sequence: int = 0,
    ) -> AsyncIterator[StoredEvent]: ...

    async def tail(
        self,
        run_id: UUID,
        after_sequence: int = 0,
    ) -> AsyncIterator[StoredEvent]: ...
```

첫 구현은 다음 두 Adapter를 제공합니다.

- `SQLiteEventJournal`: 로컬과 해커톤 실행
- `InMemoryEventJournal`: 단위 테스트와 contract test

`append()`는 expected sequence를 사용하는 compare-and-swap과 atomic batch commit을 보장합니다.
서버는 commit된 event만 stream으로 전달합니다. 느리거나 끊긴 consumer는 메모리 buffer가 아니라
Journal에서 마지막 sequence 다음부터 다시 읽습니다.

Snapshot은 replay 속도를 높이는 derived cache입니다. 정본이 아니므로 손상되면 버리고 Journal에서
다시 만듭니다. 기존 JSON/Markdown Artifact는 terminal event에서 만드는 export Adapter로 옮깁니다.

## Presentation Pipeline

### PresentationIntent

Pack Projector는 Canonical Run Event와 이전 PresentationState를 받아 protocol-neutral
`PresentationIntent`를 만듭니다.

```python
class PresentationIntent(BaseModel):
    surface_key: str
    kind: Literal[
        "open",
        "patch_components",
        "patch_data",
        "close",
        "text",
        "notice",
    ]
    catalog_key: str | None = None
    body: dict[str, JsonValue]
```

Projector는 순수 함수여야 합니다. clock, random, model 호출과 network I/O를 사용할 수 없습니다.
같은 event log와 pinned projector version은 같은 Presentation Intent를 만들어야 합니다.

### A2UIProjectionAdapter

`A2UIProjectionAdapter`는 Presentation Intent를 CopilotKit이 지원하는 exact A2UI protocol version의
message로 encode하고 공식 schema로 검증합니다. 구현 계획을 작성할 때 lockfile과 CopilotKit 공식
문서를 기준으로 protocol version을 확정합니다. Run은 선택한 protocol, Catalog와 projector version을
고정합니다.

surface ID와 component ID는 `run_id`, `surface_key`와 stable semantic key로 결정론적으로 만듭니다.
Projection 실패, protocol validation 실패와 Catalog mismatch는 Canonical Run을 실패시키지 않습니다.
Frontend에는 generic result 또는 notice fallback을 전달합니다.

A2UI message 자체는 EventJournal에 정본으로 저장하지 않습니다. reconnect 시 필요한 sequence까지
Projector를 fold한 뒤 cursor 다음 event부터 같은 A2UI update를 다시 만듭니다. 성능 문제가 확인되면
Presentation checkpoint를 내부 최적화로 추가하되 외부 Interface는 바꾸지 않습니다.

## Next.js와 CopilotKit Host

### AnalysisHost Interface

React Shell은 다음 작은 Interface만 사용합니다.

```typescript
interface AnalysisHost {
  Provider(props: HostProviderProps): React.ReactNode;
  Conversation(props: ConversationProps): React.ReactNode;
}

interface HostProviderProps {
  catalog: TrustedCatalog;
  theme: HostTheme;
  children: React.ReactNode;
}

interface ConversationProps {
  threadId?: string;
  initialPrompt?: string;
}
```

첫 두 Adapter는 다음과 같습니다.

- `CopilotKitV2HostAdapter`: production Implementation
- `FakeAnalysisHostAdapter`: Storybook과 browser test Implementation

`@copilotkit/react-core/v2`, `@copilotkit/runtime/v2`, AG-UI와 A2UI package import는
`CopilotKitV2HostAdapter`와 관련 protocol Adapter 안에서만 허용합니다. Shell은 CopilotKit hook,
AG-UI event type과 A2UI surface store를 직접 다루지 않습니다.

Next Runtime route는 Backend의 AG-UI endpoint에 연결하는 얇은 Host Adapter입니다. 대화, action,
HITL resume와 stream lifecycle은 CopilotKit과 AG-UI가 담당합니다. 프로젝트 전용 action protocol이나
별도 WebSocket을 만들지 않습니다.

### AG-UI 매핑

Backend `AGUIRunAdapter`는 다음 정보를 공식 AG-UI event로 바꿉니다.

| Backend 정보 | AG-UI 표현 |
| --- | --- |
| Run 시작, 완료, 실패 | Run lifecycle event |
| 분석 단계와 진행 상태 | Activity snapshot/delta |
| Presentation Intent | A2UI surface message |
| clarification, approval | action/HITL resume |

구현 시 exact event type과 payload는 설치한 AG-UI SDK의 공식 schema를 사용합니다. 내부 자체 event를
AG-UI와 이름만 비슷하게 복제하지 않습니다.

### Trusted Catalog

첫 trusted universal Catalog는 domain-neutral renderer로 구성합니다.

- `Text`
- `Stack`, `Grid`
- `Card`
- `Metric`
- `Table`
- `Chart`
- `Timeline`
- `Form`, `Select`
- `Button`
- `EvidenceLink`
- `Notice`

Catalog는 React renderer, strict prop schema와 theme binding을 함께 소유합니다. Pack은 Catalog key와
공개 data binding만 지정합니다. `ChurnCard`처럼 분석 이름이 들어간 renderer는 추가하지 않습니다.

완전히 새로운 상호작용 행동이 필요할 때만 Catalog 확장을 별도 변경으로 처리합니다. 기존 key를
조합해 표현 가능한 새 Pack은 Frontend를 수정하지 않습니다.

CopilotKit 내부 CSS나 `node_modules`를 patch하지 않습니다. 새 디자인은 theme token과 Catalog
renderer Implementation을 교체해 적용합니다.

## 공개 Interface와 Swagger

구현에서 FastAPI HTTP endpoint를 추가하거나 변경하면 저장소 `AGENTS.md`의 규칙을 따릅니다.

- `_OPENAPI_TAGS`의 기존 또는 신규 tag 사용
- 모든 endpoint의 `tags`와 한국어 `summary`
- Pydantic 요청/응답 모델
- `/docs` schema 노출
- 같은 변경의 `docs/api-endpoints.md` 갱신

AG-UI transport endpoint를 별도 mount해 Swagger에 노출할 수 없는 경우, 숨기는 이유를 코드와
`docs/api-endpoints.md`에 기록해야 합니다.

## 오류 처리

| 오류 | 공개 동작 |
| --- | --- |
| 알 수 없는 `pack_id` | Run 생성 전 `404 analysis_pack_not_found` |
| 잘못된 Pack input | Run 생성 전 `422`, 공개 가능한 field path |
| Run 생성 전 Journal 장애 | `503`, 성공한 Run ID 미반환 |
| Pack output schema 위반 | `run.failed`, `analysis_pack_contract_violation` |
| provider timeout | `run.failed`, `analysis_timeout` |
| 사용자 취소 | `run.failed`, `analysis_cancelled` |
| 안전한 domain 오류 | Pack이 선언한 공개 code와 message |
| 예상하지 못한 예외 | `run.failed`, `analysis_pack_failed`, 공개 correlation ID |
| Journal append 충돌 | 최신 sequence reload 후 bounded retry |
| A2UI schema 오류 | Canonical Run 유지, generic projection fallback |
| Catalog key 누락 | diagnostic card, stream 유지 |
| CopilotKit 연결 단절 | 마지막 cursor 다음부터 replay |
| 잘못된 cursor | `400 invalid_cursor` |

Pack 오류는 sanitized terminal event를 commit합니다. 이미 commit된 partial Fact는 audit와 replay를 위해
남깁니다. Projection 오류와 Host 오류는 Canonical Run 상태를 바꾸지 않습니다.

## 보안과 공개 안전성

Backend publication gate는 다음 조건을 검사합니다.

- Pack output schema와 cross-reference
- PII masking과 공개 key allowlist
- provider 원문, prompt와 private reasoning 금지
- action input schema와 expected sequence
- idempotency key와 command ID
- evidence authorization과 Run scope

Frontend rendering gate는 다음 조건을 검사합니다.

- A2UI JSON schema
- trusted Catalog allowlist
- Catalog prop schema
- 임의 HTML/JavaScript 실행 금지
- unknown component의 diagnostic fallback

LangSmith와 Langfuse trace에는 기존 저장소 규칙대로 합성 데이터와 공개 계약만 기록합니다. 새로운
Pack과 projection 단계도 공개 `run_id`, stage tag와 공개 metadata만 사용합니다.

## 테스트 전략

### AnalysisPackHarness

모든 Pack은 같은 contract test를 통과해야 합니다.

- strict fixture input validation
- emission schema와 ordering
- terminal event 1개
- 공개 데이터 안전성
- Fact와 Report cross-reference
- timeout과 cancellation
- 같은 fixture의 deterministic output schema
- projector 순수성과 idempotency

### EventJournal contract suite

`InMemoryEventJournal`과 `SQLiteEventJournal`에 같은 suite를 적용합니다.

- atomic batch append
- sequence 연속성
- expected sequence conflict
- 동일 idempotency receipt
- Backend 재시작 뒤 cursor replay
- tail 중 disconnect와 reconnect
- terminal 뒤 append 거부

### Projection과 Host 검증

- live와 replay PresentationState 동등성
- A2UI schema golden test
- duplicate frame idempotency
- unknown Catalog key fallback
- 강제 Projection 실패 중 Canonical Run 성공
- Fake Host와 CopilotKit Host contract 동등성
- browser HITL, reconnect와 accessibility
- theme 교체 뒤 Catalog 동작 유지

### E2E Golden Paths

- Fixture mode의 기존 Customer Signal 분석
- clarification 요청과 같은 Run의 resume
- Backend 재시작 뒤 진행 중 Run replay
- 완료 Run History와 JSON/Markdown export
- 두 번째 Analysis Pack의 Frontend 무변경 실행
- A2UI/CopilotKit 장애 중 generic fallback

## 점진적 전환 계획

### 1단계: Pack Kernel과 EventJournal

SQLite/Memory EventJournal과 canonical state machine을 추가합니다. 현재 HTTP endpoint와 기존
Frontend는 유지합니다.

### 2단계: CustomerSignalPack

현재 `AnalysisLoop`, analytics Primitive, Fixture와 Gemini Adapter를
`CustomerSignalPack` Implementation으로 감쌉니다. 기존 결과 계약과 fixture golden result를
유지합니다.

### 3단계: Presentation과 Host

Presentation Intent, A2UIProjectionAdapter, Backend AGUIRunAdapter와 Next
CopilotKitV2HostAdapter를 추가합니다. CopilotKit 공식 runtime과 renderer를 사용합니다.

### 4단계: 새 Frontend

새 React Shell, trusted Catalog와 theme를 구현합니다. 기존 Frontend는 E2E 비교 기준으로 유지합니다.

### 5단계: 두 번째 Pack과 Legacy 제거

두 번째 Pack을 Frontend 수정 없이 추가해 Seam을 검증합니다. 검증 뒤 Legacy runner,
generic/legacy Run 분기와 기존 Frontend domain decoder를 제거합니다. 과거 Artifact가 필요하면
read-only `LegacyArtifactAdapter`로 격리합니다.

## 제외 범위

첫 버전에서 다음 항목은 구현하지 않습니다.

- Python entry-point 자동 발견
- 폴더 plugin 자동 탐색
- WebSocket transport
- Postgres EventJournal
- Pack 전용 React renderer
- A2UI message blob 영구 저장
- multi-tenant 권한 모델
- 운영 Connector 쓰기
- 자유 SQL 또는 arbitrary code 실행

## 완료 기준

다음 조건을 모두 만족해야 구조 전환이 완료된 것으로 봅니다.

1. 새 Pack이 Pack Module과 Registry 한 줄로 등록되고 Frontend diff가 없습니다.
2. Backend 재시작과 stream 단절 뒤 cursor replay가 live state와 같습니다.
3. A2UI/CopilotKit을 강제로 실패시켜도 Canonical Run과 export가 정상 완료됩니다.
4. Frontend에 Pack ID별 switch, domain decoder와 Goal/Plan/Fact/Report union이 없습니다.
5. Fixture mode에서 전체 흐름을 외부 provider 없이 재현할 수 있습니다.
6. 두 번째 Pack이 기존 trusted Catalog만 사용해 새 Frontend에서 렌더링됩니다.
7. Backend Swagger와 `docs/api-endpoints.md`가 실제 공개 Interface와 일치합니다.
8. 기존 LangSmith/Langfuse 공개 trace 안전성 규칙을 유지합니다.

## 롤백

전환 단계마다 기존 Frontend와 기존 Run path를 유지합니다. 새 Host 또는 Projection에 문제가 생기면
기존 Frontend를 다시 사용하고 Canonical Run Event와 export는 그대로 유지합니다.

Pack Kernel 전환 문제가 확인되면 CustomerSignalPack routing만 기존 `AnalysisLoop` 직접 호출로
되돌립니다. Legacy runtime은 두 번째 Pack과 새 Frontend 검증이 끝날 때까지 삭제하지 않습니다.

## 공식 참고 자료

- [CopilotKit A2UI](https://docs.copilotkit.ai/generative-ui/a2ui)
- [A2UI Core Concepts](https://a2ui.org/concepts/overview/)
- [A2UI v1 protocol specification](https://github.com/a2ui-project/a2ui/blob/main/specification/v1_0/docs/a2ui_protocol.md)
- [AG-UI protocol overview](https://github.com/ag-ui-protocol/ag-ui/blob/main/docs/introduction.mdx)
- [AG-UI Python event models](https://github.com/ag-ui-protocol/ag-ui/blob/main/docs/sdk/python/core/events.mdx)
