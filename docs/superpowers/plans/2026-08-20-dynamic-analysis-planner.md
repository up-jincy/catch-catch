# Dynamic Analysis Planner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gemini가 자유 질문과 공개 Source/Primitive 계약으로 Plan을 만들고 Fact에 따라 다음 단계를 선택하게 합니다.
사용자는 그 근거와 수정 이력을 화면과 Run 문서에서 확인할 수 있어야 합니다.

**Architecture:** Gemini Provider에는 단순한 JSON 문서 envelope만 요청하고, 문서 안의 Goal/Plan/Note/Selection/Report를 서버의 기존 Pydantic 계약으로 검증합니다.
`AnalysisLoop`가 Plan 검증, Primitive 실행, Fact 검증과 한 번의 Plan 재작성을 소유합니다.
Runtime은 Plan revision과 Fact를 Artifact에 누적하고 Frontend는 같은 기록을 Trace, Plan, Note, 다운로드 문서에 표시합니다.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, LangChain Google GenAI, DuckDB, React 19, Next.js 16, TypeScript, Vitest, Playwright

---

## 범위 원칙

- 대표 자유 질문 한 건이 실제 Gemini로 끝까지 동작하는지 우선 확인합니다.
- 기존 10개 읽기 전용 Primitive와 Source Adapter만 사용합니다.
- 모델의 비공개 사고과정은 저장하지 않습니다. 공개 이유, 검증 Fact, 다음 행동만 남깁니다.
- Fixture 모드는 결정론적 회귀 테스트에만 사용하고 Gemini 모드의 실패를 Fixture 성공으로 바꾸지 않습니다.
- 광범위한 공격 조합과 새로운 Primitive 추가는 이번 구현에서 제외합니다.
- 각 Task에서는 집중 테스트만 실행하고 전체 Backend/Frontend/E2E는 마지막 Task에서 한 번 실행합니다.

## 파일 구조

### Backend

- `backend/src/customer_signal/domain/analysis.py`: 공개 Plan/Step/Selection/Note 설명 필드를 소유합니다.
- `backend/src/customer_signal/agent/generic_gemini.py`: Provider-safe 5단계 동적 Gemini 호출을 소유합니다.
- `backend/src/customer_signal/agent/analysis_loop.py`: 최초 Plan 재작성과 Fact 기반 다음 단계 선택을 소유합니다.
- `backend/src/customer_signal/agent/claim_validator.py`: 검증 Claim과 실제 Selection을 공개 Note로 조합합니다.
- `backend/src/customer_signal/agent/generic_fixture.py`: 기존 Fixture에 설명 필드의 결정론적 값을 제공합니다.
- `backend/src/customer_signal/runtime/events.py`: 공개 `step_started` 이벤트의 선택 이유를 검증합니다.
- `backend/src/customer_signal/runtime/run_store.py`: 현재 Plan과 전체 Plan revision 이력을 누적합니다.
- `backend/src/customer_signal/runtime/artifacts.py`: JSON Artifact와 문서 read model에 Plan 이력과 Fact를 추가합니다.
- `backend/src/customer_signal/runtime/coordinator.py`: Run checkpoint에 Plan 이력을 저장합니다.
- `backend/src/customer_signal/runtime/document_renderer.py`: Plan, Fact, Note 근거를 Markdown으로 렌더링합니다.
- `backend/src/customer_signal/api.py`: Legacy Journey 질문 외 자유 질문을 generic loop로 라우팅합니다.

### Frontend

- `frontend/src/features/customer-intelligence/contracts.ts`: 공개 설명과 Plan 이력 타입을 소유합니다.
- `frontend/src/features/customer-intelligence/run-contract-decoders.ts`: API와 Artifact의 새 필드를 fail-closed로 복원합니다.
- `frontend/src/features/customer-intelligence/run-client.ts`: `step_started` 선택 이유를 디코딩합니다.
- `frontend/src/features/customer-intelligence/run-reducer.ts`: live SSE와 Artifact hydration에서 Plan 이력을 누적합니다.
- `frontend/src/features/customer-intelligence/AnalysisPlanView.tsx`: Plan 이유, 조건, revision을 표시합니다.
- `frontend/src/features/customer-intelligence/AnalysisNoteTimeline.tsx`: 관찰 Fact와 다음 행동을 표시합니다.
- `frontend/src/features/customer-intelligence/FactDetail.tsx`: 사용 Source, 처리 통계, provenance를 표시합니다.
- `frontend/src/features/customer-intelligence/AgentTrace.tsx`: 실행 순서에 선택 이유를 표시합니다.

## Task 1: 공개 Plan과 Note 설명 계약

**Files:**

- Modify: `backend/src/customer_signal/domain/analysis.py`
- Modify: `backend/src/customer_signal/agent/claim_validator.py`
- Modify: `backend/src/customer_signal/agent/generic_fixture.py`
- Test: `backend/tests/test_analysis_contracts.py`
- Test: `backend/tests/test_claim_validator.py`

- [ ] **Step 1: 공개 설명 필드의 실패 테스트를 작성합니다.**

`backend/tests/test_analysis_contracts.py`에 다음 동작을 고정합니다.

```python
def test_plan_step_and_selection_keep_bounded_public_reasons() -> None:
    step = _step(selection_reason="Source 범위와 목표 Metric을 먼저 확인합니다.")
    plan = AnalysisPlan(
        plan_id="plan-dynamic",
        revision=0,
        goal_id="goal-1",
        rationale="Source를 확인한 뒤 행동 Segment를 비교합니다.",
        steps=[step, _profile_step(), _segment_step()],
    )

    assert plan.rationale.startswith("Source")
    assert plan.steps[0].selection_reason.startswith("Source")
    assert ContinueSelection(
        next_step_id="step-profile",
        reason="Catalog Fact에서 Source 범위를 확인했습니다.",
    ).reason.startswith("Catalog")


@pytest.mark.parametrize("value", ["", "   ", "x" * 501])
def test_public_reason_is_nonblank_and_bounded(value: str) -> None:
    with pytest.raises(ValidationError):
        ContinueSelection(next_step_id="step-profile", reason=value)
```

`backend/tests/test_claim_validator.py`에는 실제 Selection이 Note에 반영되는지 추가합니다.

```python
def test_verified_note_uses_server_selected_next_action() -> None:
    note = render_verified_note(
        _note_draft(next_step_id="step-model-proposed"),
        _fact(),
        12,
        next_step_id="step-server-selected",
        next_action="검증된 분포를 기준으로 고객 Segment를 계산합니다.",
        plan_revision=0,
    )

    assert note.next_step_id == "step-server-selected"
    assert note.next_action == "검증된 분포를 기준으로 고객 Segment를 계산합니다."
```

- [ ] **Step 2: 집중 테스트를 실행해 RED를 확인합니다.**

Run:

```bash
uv run --project backend pytest \
  backend/tests/test_analysis_contracts.py \
  backend/tests/test_claim_validator.py -q
```

Expected: `selection_reason`, `rationale`, `reason`, `next_action` 필드가 없어 실패합니다.

- [ ] **Step 3: additive 공개 계약을 구현합니다.**

`backend/src/customer_signal/domain/analysis.py`에 기존 Artifact 호환용 기본값을 가진 타입과 필드를 추가합니다.

```python
from pydantic import StringConstraints

type PublicExplanation = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]


class AnalysisStep(AnalysisContractModel):
    # limits 필드 다음에 추가합니다.
    selection_reason: PublicExplanation = (
        "분석 목표와 Source 범위에 맞는 단계를 선택했습니다."
    )


class AnalysisPlan(AnalysisContractModel):
    # steps 필드 다음에 추가합니다.
    rationale: PublicExplanation = "분석 목표를 검증 가능한 단계로 구성했습니다."


class AnalysisNote(AnalysisContractModel):
    # plan_revision 필드 다음에 추가합니다.
    next_action: PublicExplanation = "현재 단계의 검증 결과를 기록했습니다."


class ContinueSelection(AnalysisContractModel):
    kind: Literal["continue"] = "continue"
    next_step_id: str = Field(pattern=r"^step-[a-z0-9-]+$", max_length=128)
    reason: PublicExplanation = "검증된 Fact를 바탕으로 다음 단계를 계속합니다."


class StopSelection(AnalysisContractModel):
    kind: Literal["stop"] = "stop"
    reason: PublicExplanation = "분석 목표를 충족해 실행을 종료합니다."


class ReviseSelection(AnalysisContractModel):
    kind: Literal["revise"] = "revise"
    revised_plan: AnalysisPlan
    next_step_id: str = Field(pattern=r"^step-[a-z0-9-]+$", max_length=128)
    reason: PublicExplanation = "새 Fact를 반영해 미완료 Plan을 수정합니다."
```

기본값은 기존 schema-v1 Artifact를 읽기 위한 호환 경계입니다. 새 Gemini와 Fixture Plan은 각 필드에 구체적인 값을 제출합니다.

- [ ] **Step 4: Note Composer와 Fixture 값을 연결합니다.**

`backend/src/customer_signal/agent/claim_validator.py`의 함수 시그니처와 digest를 다음처럼 바꿉니다.

```python
from typing import cast

_UNSET_NEXT_STEP = object()


def render_verified_note(
    draft: AnalysisNoteDraft,
    fact: AnalysisFact,
    duration_ms: int,
    *,
    next_step_id: str | None | object = _UNSET_NEXT_STEP,
    next_action: str = "현재 단계의 검증 결과를 기록했습니다.",
    plan_revision: int = 0,
) -> AnalysisNote:
    selected_step_id = (
        draft.next_step_id
        if next_step_id is _UNSET_NEXT_STEP
        else cast(str | None, next_step_id)
    )
    if selected_step_id == fact.step_id:
        raise ClaimValidationError("note next step cannot select the completed step")
    # 기존 Claim 검증과 시간 계산은 유지합니다.
    note_operands = {
        "step_id": fact.step_id,
        "fact_id": fact.fact_id,
        "claim_ids": claim_ids,
        "next_step_id": selected_step_id,
        "next_action": next_action,
        "plan_revision": plan_revision,
    }
    # 기존 sha256 digest를 사용합니다.
    return AnalysisNote(
        # 기존 서버 소유 필드는 유지합니다.
        next_step_id=selected_step_id,
        next_action=next_action,
    )
```

`backend/src/customer_signal/agent/generic_fixture.py`의 Plan, Step, Selection에도 시나리오에 맞는 결정론적 이유를 넣습니다.

```python
return AnalysisPlan(
    plan_id=f"plan-{scenario}",
    revision=0,
    goal_id=goal.goal_id,
    rationale="Source 범위를 확인한 뒤 질문에 필요한 고객 신호를 단계별로 계산합니다.",
    steps=steps,
)

return ContinueSelection(
    next_step_id=next_step.step_id,
    reason=f"직전 Fact를 확인해 {next_step.primitive} 단계를 계속합니다.",
)
```

- [ ] **Step 5: 집중 테스트를 GREEN으로 만듭니다.**

Run:

```bash
uv run --project backend pytest \
  backend/tests/test_analysis_contracts.py \
  backend/tests/test_claim_validator.py \
  backend/tests/test_analysis_loop.py -q
```

Expected: 모든 테스트가 통과합니다.

- [ ] **Step 6: 계약 변경을 커밋합니다.**

```bash
git add \
  backend/src/customer_signal/domain/analysis.py \
  backend/src/customer_signal/agent/claim_validator.py \
  backend/src/customer_signal/agent/generic_fixture.py \
  backend/tests/test_analysis_contracts.py \
  backend/tests/test_claim_validator.py
git commit -m "feat: (domain) 공개 Planner 근거 계약 추가"
```

## Task 2: Provider-safe Gemini 동적 5단계 호출

**Files:**

- Modify: `backend/src/customer_signal/agent/generic_gemini.py`
- Modify: `backend/src/customer_signal/api.py`
- Test: `backend/tests/test_generic_gemini.py`
- Test: `backend/tests/test_runtime_generic.py`

- [ ] **Step 1: 고정 Scenario 위임을 깨는 실패 테스트를 작성합니다.**

`backend/tests/test_generic_gemini.py`의 기존 Scenario 전용 테스트를 5개 JSON 문서 응답 테스트로 교체합니다.

```python
@pytest.mark.asyncio
async def test_free_question_uses_five_flat_provider_documents() -> None:
    request, manifests, goal, plan, step_context, note_draft, selection_context, selection, report_context, report_draft = (
        await _staged_values(question="부정 피드백 고객은 이후 어떤 행동을 보이고 일반 고객과 무엇이 달라?")
    )
    provider = _ScriptedProvider(
        {
            "gemini-3.7-flash": [
                {"document": goal.model_dump_json()},
                {"document": plan.model_dump_json()},
                {"document": note_draft.model_dump_json()},
                {"document": selection.model_dump_json()},
                {"document": report_draft.model_dump_json()},
            ]
        }
    )
    model = GeminiAnalysisModel(api_key="key", model_factory=provider)

    assert await model.create_goal(request, manifests) == goal
    assert await model.create_plan(goal, manifests) == plan
    assert await model.create_note(step_context) == note_draft
    assert await model.select_next(selection_context) == selection
    assert await model.create_report(report_context) == report_draft

    assert [call["schema_title"] for call in provider.structured_calls] == [
        "GoalDecisionDocument",
        "AnalysisPlanDocument",
        "AnalysisNoteDraftDocument",
        "StepSelectionDocument",
        "CustomerSignalReportDraftDocument",
    ]
    assert all(
        token not in json.dumps(call["schema"], sort_keys=True)
        for call in provider.structured_calls
        for token in ("$defs", "$ref", "oneOf", "discriminator")
    )
    prompts = "\n".join(call["prompt"] for call in provider.structured_calls)
    assert request.question in prompts
    assert NEGATIVE_TOPIC_QUESTION not in prompts
    assert "catalog_sources" in prompts
    assert "get_evidence" in prompts
```

잘못된 JSON 문서도 공개 결과로 통과하지 않는지 추가합니다.

```python
@pytest.mark.asyncio
async def test_invalid_domain_document_fails_with_safe_validation_error() -> None:
    provider = _ScriptedProvider({"gemini-3.7-flash": [{"document": "{}"}]})
    model = GeminiAnalysisModel(api_key="key", model_factory=provider)

    with pytest.raises(GeminiAnalysisError) as caught:
        await model.create_goal(_request(), [_manifest()])

    assert caught.value.code == "gemini_validation_failed"
```

- [ ] **Step 2: Gemini 집중 테스트를 실행해 RED를 확인합니다.**

Run:

```bash
uv run --project backend pytest backend/tests/test_generic_gemini.py -q
```

Expected: Provider 호출이 Scenario 한 번뿐이고 나머지 단계가 Fixture에 위임되어 실패합니다.

- [ ] **Step 3: Flat JSON document envelope를 구현합니다.**

`backend/src/customer_signal/agent/generic_gemini.py`에서 `_AnalysisScenarioDecision`을 제거하고 다음 경계를 추가합니다.

```python
class _JsonDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    document: str = Field(min_length=2, max_length=120_000)


async def _invoke_document(
    self,
    *,
    output_type: Any,
    schema_title: str,
    stage: str,
    public_input: dict[str, Any],
    allow_initial_fallback: bool = False,
) -> Any:
    target = TypeAdapter(output_type)
    envelope = await self._invoke(
        output_type=_JsonDocument,
        schema_title=f"{schema_title}Document",
        prompt=_stage_prompt(
            stage,
            {
                **public_input,
                "target_schema": target.json_schema(),
            },
        ),
        allow_initial_fallback=allow_initial_fallback,
    )
    try:
        return target.validate_json(envelope.document)
    except (ValidationError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise GeminiAnalysisError(
            "gemini_validation_failed",
            "Gemini 구조화 분석 결과 검증에 실패했습니다.",
        ) from error
```

Provider의 `with_structured_output`에는 `_JsonDocument`의 평탄한 schema만 전달합니다. 실제 도메인 schema는 prompt에만 넣고 서버에서 다시 검증합니다.

- [ ] **Step 4: 5개 모델 단계를 실제 Gemini 호출로 교체합니다.**

다음 형태로 각 public context를 전달합니다.

```python
async def create_goal(self, request, manifests) -> GoalDecision:
    guard = await GenericFixtureModel().create_goal(request, manifests)
    if isinstance(guard, UnsupportedAnalysis) and guard.code == "pii_request":
        return guard
    return await self._invoke_document(
        output_type=GoalDecision,
        schema_title="GoalDecision",
        stage="goal",
        public_input={
            "request": request.model_dump(mode="json"),
            "sources": _public_manifests(manifests),
            "primitive_catalog": _primitive_catalog(),
        },
        allow_initial_fallback=True,
    )


async def create_plan(
    self,
    goal: AnalysisGoal,
    manifests: list[SourceManifest],
    *,
    validation_feedback: str | None = None,
) -> AnalysisPlan:
    return await self._invoke_document(
        output_type=AnalysisPlan,
        schema_title="AnalysisPlan",
        stage="plan",
        public_input={
            "goal": goal.model_dump(mode="json"),
            "sources": _public_manifests(manifests),
            "primitive_catalog": _primitive_catalog(),
            "validation_feedback": validation_feedback,
            "constraints": {
                "step_count": "3..6",
                "first_step_should_discover_sources": True,
                "read_only": True,
            },
        },
    )
```

`create_note`, `select_next`, `create_report`도 `_invoke_document`를 사용합니다.
입력에는 이미 공개 가능한 `AnalysisFact.model_dump(mode="json")`와 `AnalysisNote.model_dump(mode="json")`만 넣습니다.
API Key, Provider 응답 원문, Identity namespace, 내부 Manifest는 prompt에 넣지 않습니다.

`_primitive_catalog()`는 `PRIMITIVE_INPUT_ADAPTER.json_schema()`와 다음 10개 이름을 반환합니다.

```python
def _primitive_catalog() -> dict[str, Any]:
    return {
        "names": [
            "catalog_sources",
            "profile_events",
            "aggregate_events",
            "segment_customers",
            "detect_repetition",
            "match_sequence",
            "compare_segments",
            "rank_customers",
            "get_customer_journey",
            "get_evidence",
        ],
        "input_schema": PRIMITIVE_INPUT_ADAPTER.json_schema(),
    }
```

- [ ] **Step 5: API의 Fixture delegate 주입을 제거합니다.**

`backend/src/customer_signal/api.py`에서 Gemini loop 생성 코드를 다음처럼 단순화합니다.

```python
generic_gemini_loop = AnalysisLoop(
    model=GeminiAnalysisModel(
        api_key=api_key,
        primary_model=settings.gemini_model,
        fallback_model=settings.gemini_fallback_model,
    ),
    executor=executor,
    registry=registry,
)
```

`backend/tests/test_runtime_generic.py`의 고정 Plan 동일성 테스트는 Gemini model이 `GenericFixtureModel` delegate를 보유하지 않는지 확인하도록 바꿉니다.

- [ ] **Step 6: Gemini와 API 경계 테스트를 GREEN으로 만듭니다.**

Run:

```bash
uv run --project backend pytest \
  backend/tests/test_generic_gemini.py \
  backend/tests/test_runtime_generic.py -q
```

Expected: 5개 staged call, 3.7에서 typed `NOT_FOUND`인 첫 호출만 3.6 fallback, timeout/cancel 회귀가 모두 통과합니다.

- [ ] **Step 7: 동적 Gemini 단계를 커밋합니다.**

```bash
git add \
  backend/src/customer_signal/agent/generic_gemini.py \
  backend/src/customer_signal/api.py \
  backend/tests/test_generic_gemini.py \
  backend/tests/test_runtime_generic.py
git commit -m "feat: (agent) Gemini 동적 분석 단계 추가"
```

## Task 3: 최초 Plan 재작성과 Fact 기반 다음 단계

**Files:**

- Modify: `backend/src/customer_signal/agent/analysis_loop.py`
- Modify: `backend/src/customer_signal/agent/generic_fixture.py`
- Modify: `backend/src/customer_signal/runtime/events.py`
- Test: `backend/tests/test_analysis_loop.py`
- Test: `backend/tests/test_runtime_generic.py`

- [ ] **Step 1: Plan 재작성과 adaptive revision의 실패 테스트를 작성합니다.**

`backend/tests/test_analysis_loop.py`에 두 모델을 추가합니다.

```python
class RepairingModel(_FakeModel):
    def __init__(self, invalid_plan: AnalysisPlan, valid_plan: AnalysisPlan) -> None:
        super().__init__()
        self.plans = [invalid_plan, valid_plan]
        self.feedback: list[str | None] = []

    async def create_plan(self, goal, manifests, *, validation_feedback=None):
        self.feedback.append(validation_feedback)
        return self.plans.pop(0)


@pytest.mark.asyncio
async def test_invalid_initial_plan_is_rewritten_once_before_execution() -> None:
    model = RepairingModel(_plan_with_unknown_source(), _valid_plan())
    executor = _RecordingExecutor()
    events, outcome = await _run(model=model, executor=executor)

    assert outcome.status == "completed"
    assert model.feedback[0] is None
    assert "unknown" in model.feedback[1].casefold()
    assert executor.step_ids == [step.step_id for step in _valid_plan().steps]
    assert [event.type for event in events].count("plan_created") == 1


@pytest.mark.asyncio
async def test_second_invalid_plan_fails_without_executing_a_primitive() -> None:
    model = RepairingModel(_plan_with_unknown_source(), _plan_with_unknown_source())
    executor = _RecordingExecutor()
    events, outcome = await _run(model=model, executor=executor)

    assert outcome.status == "failed"
    assert executor.step_ids == []
    assert all(event.type != "plan_created" for event in events)
```

Fact 값에 따라 revision을 선택하는 모델도 고정합니다.

```python
@pytest.mark.asyncio
async def test_fact_can_revise_unfinished_plan_and_publish_reason() -> None:
    model = _CatalogDrivenRevisionModel()
    events, outcome = await _run(model=model)

    assert outcome.status == "completed"
    assert outcome.plan.revision == 1
    assert [event.type for event in events].count("plan_revised") == 1
    revised = next(event for event in events if event.type == "plan_revised")
    assert revised.payload["plan"]["rationale"] == "Catalog Fact에서 VOC와 행동 Source를 확인해 비교 단계를 추가합니다."
    first_note = next(event for event in events if event.type == "analysis_note_created")
    assert first_note.payload["note"]["next_action"].startswith("Catalog Fact")
```

- [ ] **Step 2: AnalysisLoop 집중 테스트를 실행해 RED를 확인합니다.**

Run:

```bash
uv run --project backend pytest backend/tests/test_analysis_loop.py -q
```

Expected: 첫 invalid Plan에서 바로 실패하고, `next_action`과 `selection_reason` 이벤트가 없어 실패합니다.

- [ ] **Step 3: 최초 Plan을 한 번만 재작성합니다.**

`AnalysisModel` Protocol과 Fixture의 `create_plan`에 `validation_feedback` keyword를 추가합니다. Fixture는 값을 사용하지 않습니다.

`backend/src/customer_signal/agent/analysis_loop.py`에 다음 helper를 추가합니다.

```python
async def _create_validated_plan(
    model: AnalysisModel,
    goal: AnalysisGoal,
    manifests: list[SourceManifest],
) -> AnalysisPlan:
    feedback: str | None = None
    for attempt in range(2):
        plan = await model.create_plan(
            goal,
            manifests,
            validation_feedback=feedback,
        )
        try:
            if plan.goal_id != goal.goal_id:
                raise PlanValidationError("Plan goal_id must equal the validated Goal")
            validate_plan(plan, manifests)
            _validate_plan_scope(plan, goal)
            return plan
        except (PlanValidationError, ValueError) as error:
            if attempt == 1:
                raise PlanValidationError(str(error)) from error
            feedback = str(error)[:500]
    raise AssertionError("bounded Plan repair loop exhausted")
```

`run()`은 검증된 Plan만 `plan_created`로 emit합니다. 두 번째 Plan도 실패하면 기존 safe error 경계가 failed outcome과 Artifact를 만듭니다.

- [ ] **Step 4: 실제 Selection을 먼저 확정한 뒤 Note를 공개합니다.**

현재 `fact_created` 뒤의 순서를 다음으로 바꿉니다.

```python
draft = await self._model.create_note(step_context)
selection: StepSelection | None = None
selected_next_step_id: str | None = None
next_action = "계획한 분석 단계를 모두 완료했습니다."
revised_plan: AnalysisPlan | None = None

if _server_stop_requested(step, fact):
    next_action = "서버 종료 조건을 충족해 분석을 마칩니다."
elif remaining:
    selection = await self._model.select_next(selection_context)
    next_action = selection.reason
    if isinstance(selection, StopSelection):
        selected_next_step_id = None
    elif isinstance(selection, ContinueSelection):
        _select_ready_step(plan, selection.next_step_id, completed_step_ids | {step.step_id})
        selected_next_step_id = selection.next_step_id
    elif isinstance(selection, ReviseSelection):
        validate_plan_revision(
            previous=plan,
            revised=selection.revised_plan,
            completed_step_ids=completed_step_ids | {step.step_id},
            manifests=manifests,
        )
        _validate_plan_scope(selection.revised_plan, goal)
        _select_ready_step(
            selection.revised_plan,
            selection.next_step_id,
            completed_step_ids | {step.step_id},
        )
        revised_plan = selection.revised_plan
        selected_next_step_id = selection.next_step_id

note = render_verified_note(
    draft,
    fact,
    duration_ms,
    next_step_id=selected_next_step_id,
    next_action=next_action,
    plan_revision=plan.revision,
)
```

Note와 `step_completed`를 emit한 뒤 `revised_plan`이 있으면 `plan_revised`를 emit하고 현재 Plan을 교체합니다. 이렇게 하면 화면의 다음 행동과 실제 실행이 일치합니다.

- [ ] **Step 5: `step_started`에 선택 이유를 넣습니다.**

`backend/src/customer_signal/runtime/events.py`를 다음처럼 확장하고 `AnalysisLoop` emit에도 같은 값을 넣습니다.

```python
class StepStartedPayload(GenericEventContract):
    step_id: str = Field(min_length=1, max_length=128)
    primitive: GenericPrimitiveName
    selection_reason: str = Field(min_length=1, max_length=500)
    started_at: AwareDatetime
```

- [ ] **Step 6: Adaptive loop 테스트를 GREEN으로 만듭니다.**

Run:

```bash
uv run --project backend pytest \
  backend/tests/test_analysis_loop.py \
  backend/tests/test_plan_validator.py \
  backend/tests/test_runtime_generic.py -q
```

Expected: Plan repair 1회, second failure no-execution, Continue/Stop/Revise, 공개 이벤트 순서가 통과합니다.

- [ ] **Step 7: 실행 변경을 커밋합니다.**

```bash
git add \
  backend/src/customer_signal/agent/analysis_loop.py \
  backend/src/customer_signal/agent/generic_fixture.py \
  backend/src/customer_signal/runtime/events.py \
  backend/tests/test_analysis_loop.py \
  backend/tests/test_runtime_generic.py
git commit -m "feat: (agent) Fact 기반 Plan 수정 실행"
```

## Task 4: Plan revision과 Fact 문서 기록

**Files:**

- Modify: `backend/src/customer_signal/runtime/artifacts.py`
- Modify: `backend/src/customer_signal/runtime/run_store.py`
- Modify: `backend/src/customer_signal/runtime/coordinator.py`
- Modify: `backend/src/customer_signal/runtime/document_renderer.py`
- Test: `backend/tests/test_artifact_store.py`
- Test: `backend/tests/test_document_renderer.py`
- Test: `backend/tests/test_runtime_generic.py`

- [ ] **Step 1: revision 보존과 Markdown 근거의 실패 테스트를 작성합니다.**

```python
def test_run_store_keeps_created_and_revised_plans_in_order() -> None:
    store, run_id = _generic_store()
    _append_generic(store, run_id, "plan_created", {"plan": _plan(0).model_dump(mode="json")})
    _append_generic(store, run_id, "plan_revised", {"plan": _plan(1).model_dump(mode="json")})

    snapshot = store.get_snapshot(run_id)
    assert [plan.revision for plan in snapshot.plan_history] == [0, 1]
    assert snapshot.plan == snapshot.plan_history[-1]


def test_markdown_records_plan_history_fact_and_next_action() -> None:
    artifact = _artifact(
        plan=_plan(1),
        plan_history=[_plan(0), _plan(1)],
        facts=[_fact()],
        notes=[_note(next_action="비교 Segment를 실행합니다.")],
    )
    markdown = render_markdown(artifact)

    assert "revision 0" in markdown
    assert "revision 1" in markdown
    assert "선택 이유" in markdown
    assert "스캔" in markdown
    assert "비교 Segment를 실행합니다." in markdown
```

기존 schema-v1 JSON에 `plan_history`가 없어도 현재 Plan을 fallback으로 읽는 테스트도 추가합니다.

- [ ] **Step 2: Runtime/Artifact 집중 테스트를 실행해 RED를 확인합니다.**

Run:

```bash
uv run --project backend pytest \
  backend/tests/test_artifact_store.py \
  backend/tests/test_document_renderer.py \
  backend/tests/test_runtime_generic.py -q
```

Expected: `plan_history`와 Document `facts`가 없어 실패합니다.

- [ ] **Step 3: Runtime에 Plan history를 additive로 저장합니다.**

`backend/src/customer_signal/runtime/artifacts.py`에 기본값을 둡니다.

```python
class RunArtifact(ArtifactContractModel):
    # plan 필드 다음에 추가합니다.
    plan: AnalysisPlan | None = None
    plan_history: list[AnalysisPlan] = Field(default_factory=list, max_length=32)


class ArtifactDocument(ArtifactContractModel):
    # plan 필드 다음에 추가합니다.
    plan: AnalysisPlan | None = None
    plan_history: list[AnalysisPlan] = Field(default_factory=list, max_length=32)
    facts: list[AnalysisFact] = Field(default_factory=list, max_length=128)
```

Artifact validator는 history가 있을 때 revision이 증가하고 마지막 값이 `plan`과 같은지만 확인합니다.

```python
if self.plan_history:
    revisions = [item.revision for item in self.plan_history]
    if revisions != sorted(set(revisions)):
        raise ValueError("Artifact Plan revisions must be unique and increasing")
    if self.plan != self.plan_history[-1]:
        raise ValueError("Artifact current Plan must equal the last revision")
```

`RunSnapshot`과 `_RunState`에도 `plan_history`를 기본 빈 list로 추가합니다. `plan_created`와 `plan_revised` 처리 시 같은 `plan_id/revision`을 중복 append하지 않습니다.

```python
plan = AnalysisPlan.model_validate_json(_json(payload["plan"]))
state.plan = plan
if not state.plan_history or state.plan_history[-1].revision != plan.revision:
    state.plan_history = [*state.plan_history, plan]
```

Artifact restore는 `artifact.plan_history or ([artifact.plan] if artifact.plan else [])`를 사용합니다. Legacy snapshot serializer에서는 `plan_history`도 제거합니다.

- [ ] **Step 4: Checkpoint와 Document projection을 연결합니다.**

`backend/src/customer_signal/runtime/coordinator.py`의 `RunArtifact(...)`에 다음 값을 추가합니다.

```python
plan_history=snapshot.plan_history,
```

`backend/src/customer_signal/runtime/document_renderer.py`의 `ArtifactDocument(...)`에 다음 값을 넣습니다.

```python
plan_history=(
    list(artifact.plan_history)
    if artifact.plan_history
    else ([artifact.plan] if artifact.plan is not None else [])
),
facts=list(artifact.facts),
```

- [ ] **Step 5: Markdown에 Plan, 조건, Fact, Note를 렌더링합니다.**

`_render_plan`은 history를 순서대로 렌더링합니다.

```python
for plan in document.plan_history:
    lines.extend(
        [
            "",
            f"### Plan revision {_inline(plan.revision)}",
            "",
            f"- Reason: {_inline(plan.rationale)}",
        ]
    )
    for step in plan.steps:
        parameters = json.dumps(
            step.parameters.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        lines.extend(
            [
                f"- Step: {_inline(step.step_id)} / {_inline(step.primitive)}",
                f"- Sources: {_joined(step.source_ids)}",
                f"- Selection reason: {_inline(step.selection_reason)}",
                f"- Parameters: `{_inline(parameters)}`",
            ]
        )
```

새 `_render_facts`를 `_render_plan`과 `_render_notes` 사이에 호출합니다.
각 Fact의 primitive, Source, metrics, scanned/matched/returned, result ID를 출력합니다.
`_render_notes`에는 Verified Claim과 `note.next_action`을 출력합니다.

- [ ] **Step 6: 기록 테스트를 GREEN으로 만듭니다.**

Run:

```bash
uv run --project backend pytest \
  backend/tests/test_artifact_store.py \
  backend/tests/test_document_renderer.py \
  backend/tests/test_runtime_generic.py -q
```

Expected: live SSE, checkpoint, 재시작 restore, JSON, Markdown에서 revision과 Fact가 보존됩니다.

- [ ] **Step 7: 기록 변경을 커밋합니다.**

```bash
git add \
  backend/src/customer_signal/runtime/artifacts.py \
  backend/src/customer_signal/runtime/run_store.py \
  backend/src/customer_signal/runtime/coordinator.py \
  backend/src/customer_signal/runtime/document_renderer.py \
  backend/tests/test_artifact_store.py \
  backend/tests/test_document_renderer.py \
  backend/tests/test_runtime_generic.py
git commit -m "feat: (runtime) Plan 수정 이력 기록"
```

## Task 5: 오른쪽 Workspace에 선택 이유와 관찰 Fact 표시

**Files:**

- Modify: `frontend/src/features/customer-intelligence/contracts.ts`
- Modify: `frontend/src/features/customer-intelligence/run-contract-decoders.ts`
- Modify: `frontend/src/features/customer-intelligence/run-client.ts`
- Modify: `frontend/src/features/customer-intelligence/run-reducer.ts`
- Modify: `frontend/src/features/customer-intelligence/AnalysisWorkspace.tsx`
- Modify: `frontend/src/features/customer-intelligence/AnalysisPlanView.tsx`
- Modify: `frontend/src/features/customer-intelligence/AnalysisNoteTimeline.tsx`
- Modify: `frontend/src/features/customer-intelligence/FactDetail.tsx`
- Modify: `frontend/src/features/customer-intelligence/AgentTrace.tsx`
- Modify: `frontend/src/app/globals.css`
- Modify: `frontend/src/features/customer-intelligence/__tests__/generic-fixtures.ts`
- Test: `frontend/src/features/customer-intelligence/__tests__/run-client.test.ts`
- Test: `frontend/src/features/customer-intelligence/__tests__/run-reducer.test.ts`
- Test: `frontend/src/features/customer-intelligence/__tests__/AnalysisWorkspace.test.tsx`
- Test: `frontend/src/features/customer-intelligence/__tests__/AgentTrace.test.tsx`

- [ ] **Step 1: 사용자에게 보이는 근거의 실패 테스트를 작성합니다.**

`AnalysisWorkspace.test.tsx`에서 다음 텍스트를 고정합니다.

```tsx
expect(screen.getByText("행동 Source를 먼저 탐색합니다.")).toBeVisible();
expect(screen.getByText("부정 피드백 Segment와 일반 고객을 비교하기 위해 선택했습니다.")).toBeVisible();
expect(screen.getByText("Negative Feedback Customers: 6 customers")).toBeVisible();
expect(screen.getByText("두 Segment의 반복 행동 분포를 비교합니다.")).toBeVisible();
expect(screen.getByText("revision 0 → 1")).toBeVisible();
expect(screen.getByText(/스캔 199.*매칭 30.*반환 6/)).toBeVisible();
```

`run-reducer.test.ts`에는 live event와 Artifact hydrate가 같은 history를 만드는지 추가합니다.

```ts
expect(state.planHistory.map((plan) => plan.revision)).toEqual([0, 1]);
expect(state.plan).toEqual(state.planHistory.at(-1));
```

`run-client.test.ts`는 `step_started.data.selection_reason` 누락이나 `null`을 거부하고 정상 string을 보존해야 합니다.

- [ ] **Step 2: Frontend 집중 테스트를 실행해 RED를 확인합니다.**

Run:

```bash
npm --prefix frontend test -- --run \
  src/features/customer-intelligence/__tests__/run-client.test.ts \
  src/features/customer-intelligence/__tests__/run-reducer.test.ts \
  src/features/customer-intelligence/__tests__/AnalysisWorkspace.test.tsx \
  src/features/customer-intelligence/__tests__/AgentTrace.test.tsx
```

Expected: decoder, reducer, 화면에 새 필드가 없어 실패합니다.

- [ ] **Step 3: TypeScript 계약과 decoder를 확장합니다.**

`contracts.ts`를 다음처럼 확장합니다.

```ts
export interface AnalysisStep {
  // limits 다음에 추가합니다.
  selection_reason: string;
}

export interface AnalysisPlan {
  // steps 다음에 추가합니다.
  rationale: string;
}

export interface AnalysisNote {
  // plan_revision 다음에 추가합니다.
  next_action: string;
}

export interface RunArtifact {
  // plan 다음에 추가합니다.
  plan_history: AnalysisPlan[];
}

export interface RunSnapshot {
  // plan 다음에 추가합니다.
  plan_history: AnalysisPlan[];
}

export interface ArtifactDocument {
  // plan 다음에 추가합니다.
  plan_history: AnalysisPlan[];
  facts: AnalysisFact[];
}
```

`run-contract-decoders.ts`는 string 필드를 검증합니다.
기존 Artifact에 `plan_history`가 없으면 `plan ? [plan] : []`를 사용합니다.
현재 API 응답에서 값이 존재하지만 타입이 틀리면 fallback하지 않고 계약 오류를 반환합니다.

- [ ] **Step 4: Reducer가 Plan history를 누적하도록 합니다.**

```ts
export interface RunState {
  // plan 다음에 추가합니다.
  planHistory: AnalysisPlan[];
}

function appendPlan(history: AnalysisPlan[], plan: AnalysisPlan) {
  if (history.some((item) => item.plan_id === plan.plan_id && item.revision === plan.revision)) {
    return history;
  }
  return [...history, plan];
}
```

`plan_created`와 `plan_revised`에서 `planHistory`를 append하고, `hydrate_artifact`에서는 `artifact.plan_history`를 복사합니다.

- [ ] **Step 5: Plan, Fact, Note, Trace를 사람이 읽을 수 있게 표시합니다.**

`AnalysisPlanView.tsx`에는 다음 요소를 추가합니다.

```tsx
<p className="plan-rationale">{plan.rationale}</p>
<p className="plan-history-label">
  revision {planHistory.map((item) => item.revision).join(" → ")}
</p>
```

각 Step에는 `selection_reason`, Source와 JSON으로 직렬화한 구조화 parameters를 표시합니다. parameters는 React text node로 렌더링하고 `dangerouslySetInnerHTML`을 사용하지 않습니다.

`AnalysisNoteTimeline.tsx`는 Claim을 `관찰 Fact` 제목 아래 두고 다음 행동을 별도 문단으로 표시합니다.

```tsx
<h4>관찰 Fact</h4>
{note.claims.map((claim) => (
  <blockquote key={claim.claim_id}>{claim.rendered_text}</blockquote>
))}
<p className="note-next-action">
  <strong>다음 행동</strong> {note.next_action}
</p>
```

`FactDetail.tsx`에는 Fact provenance의 기간과 dataset/adapter version을 접을 수 있는 세부 정보로 추가합니다.
`AgentTrace.tsx`는 `step_started`의 상세 문구로 `selection_reason`을 사용합니다.

- [ ] **Step 6: Frontend 집중 테스트와 typecheck를 GREEN으로 만듭니다.**

Run:

```bash
npm --prefix frontend test -- --run \
  src/features/customer-intelligence/__tests__/run-client.test.ts \
  src/features/customer-intelligence/__tests__/run-reducer.test.ts \
  src/features/customer-intelligence/__tests__/AnalysisWorkspace.test.tsx \
  src/features/customer-intelligence/__tests__/AgentTrace.test.tsx
npm --prefix frontend run typecheck
```

Expected: 테스트와 typecheck가 모두 통과합니다.

- [ ] **Step 7: Workspace 변경을 커밋합니다.**

```bash
git add \
  frontend/src/features/customer-intelligence \
  frontend/src/app/globals.css
git commit -m "feat: (frontend) 동적 분석 근거 표시"
```

## Task 6: 자유 질문 라우팅, 기능 E2E, 실제 Gemini smoke

**Files:**

- Modify: `backend/src/customer_signal/api.py`
- Test: `backend/tests/test_api.py`
- Test: `backend/tests/test_runtime_generic.py`
- Create: `frontend/e2e/live-gemini-planner.spec.ts`
- Modify: `frontend/e2e/generic-analysis.spec.ts`
- Modify: `docs/verification/live-gemini-smoke.md`

- [ ] **Step 1: 자유 질문 라우팅 실패 테스트를 작성합니다.**

`backend/tests/test_api.py`에 다음 경계를 추가합니다.

```python
@pytest.mark.parametrize(
    "question",
    [
        "부정 피드백 고객은 이후 어떤 행동을 보이고 일반 고객과 무엇이 달라?",
        "최근 이탈 고객의 공통 행동 경로를 Source별로 비교해줘.",
    ],
)
def test_freeform_analysis_questions_route_to_generic_loop(question: str) -> None:
    response = client.post("/api/runs", json=_payload(question=question))
    assert response.status_code == 202
    run_id = response.json()["run_id"]
    assert _wait_snapshot(client, run_id)["run_kind"] == "generic"


def test_bounded_legacy_journey_question_keeps_legacy_route() -> None:
    response = client.post("/api/runs", json=_payload(question=LEGACY_JOURNEY_QUESTION))
    assert response.status_code == 202
    assert _wait_snapshot(client, response.json()["run_id"])["run_kind"] == "legacy"
```

- [ ] **Step 2: API 집중 테스트를 실행해 RED를 확인합니다.**

Run:

```bash
uv run --project backend pytest \
  backend/tests/test_api.py::test_freeform_analysis_questions_route_to_generic_loop \
  backend/tests/test_api.py::test_bounded_legacy_journey_question_keeps_legacy_route -q
```

Expected: 자유 질문이 legacy path로 들어가 실패합니다.

- [ ] **Step 3: Legacy Journey 외 질문을 generic으로 라우팅합니다.**

`backend/src/customer_signal/api.py`를 다음처럼 단순화합니다.

```python
def _is_generic_question(question: str) -> bool:
    return not is_supported_target_journey_question(question)
```

명시적인 `?mode=fixture`와 `?mode=gemini`는 generic 실행 모드를 계속 선택합니다. PII와 unsupported 질문도 generic Goal guard가 실행 전에 안전하게 거부합니다.

- [ ] **Step 4: 결정론적 기능 E2E에 기록 확인을 추가합니다.**

`frontend/e2e/generic-analysis.spec.ts`의 기존 Fixture Run에 다음 assertion을 추가합니다.

```ts
await expect(workspace.getByText(/선택.*이유|선택했습니다/).first()).toBeVisible();
await expect(workspace.getByText("관찰 Fact", { exact: true }).first()).toBeVisible();
await expect(workspace.getByText("다음 행동", { exact: true }).first()).toBeVisible();
```

다운로드 검증은 JSON의 `plan_history`, `facts`, `notes[].next_action`과 Markdown의 `Plan revision`, `검증 Fact`, `다음 행동`을 확인합니다.

- [ ] **Step 5: 실제 Gemini 전용 Playwright smoke를 작성합니다.**

`frontend/e2e/live-gemini-planner.spec.ts`는 명시적인 환경 변수 없이는 skip하고, 외부에서 시작한 env-backed 서버를 재사용합니다.

```ts
import { expect, test } from "@playwright/test";

const backendUrl = `http://127.0.0.1:${process.env.E2E_BACKEND_PORT ?? "38100"}`;

test.skip(
  process.env.LIVE_GEMINI_E2E !== "1",
  "실제 Gemini 호출은 명시적인 smoke에서만 실행합니다.",
);

test("자유 질문을 동적 Plan으로 분석하고 기록한다", async ({ page }) => {
  test.setTimeout(180_000);
  const question =
    "최근 부정적인 피드백을 남긴 고객은 이후 어떤 행동 패턴을 보이고, 일반 고객과 무엇이 달라?";
  await page.goto("/");
  await page.getByRole("textbox", { name: "분석 질문", exact: true }).fill(question);
  const acceptedResponse = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/runs" &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "분석 시작" }).click();
  const accepted = (await (await acceptedResponse).json()) as { run_id: string };

  const trace = page.getByRole("list", { name: "공개 Agent 실행 기록" });
  await expect(trace.getByText("Run 완료", { exact: true })).toBeVisible({ timeout: 120_000 });
  const workspace = page.getByRole("region", { name: "분석 Workspace" });
  await expect(workspace.getByText("관찰 Fact", { exact: true }).first()).toBeVisible();
  await expect(workspace.getByText("다음 행동", { exact: true }).first()).toBeVisible();
  await expect.poll(() => workspace.locator(".analysis-step").count()).toBeGreaterThanOrEqual(3);

  const artifactResponse = await page.request.get(
    `${backendUrl}/api/run-artifacts/${accepted.run_id}`,
  );
  expect(artifactResponse.ok()).toBe(true);
  const artifact = (await artifactResponse.json()) as {
    plan_history: Array<{
      revision: number;
      steps: Array<{ primitive: string; source_ids: string[] }>;
    }>;
    facts: unknown[];
    notes: Array<{ next_action: string }>;
  };
  const finalPlan = artifact.plan_history.at(-1)!;
  expect(new Set(finalPlan.steps.map((step) => step.primitive)).size).toBeGreaterThanOrEqual(2);
  expect(new Set(finalPlan.steps.flatMap((step) => step.source_ids)).size).toBeGreaterThanOrEqual(2);
  expect(artifact.facts.length).toBeGreaterThanOrEqual(2);
  expect(artifact.notes.every((note) => note.next_action.length > 0)).toBe(true);
});
```

Smoke 실행은 기존 Playwright config의 `PLAYWRIGHT_REUSE_SERVER=1`을 사용하므로 Fixture backend를 새로 띄우지 않습니다.

- [ ] **Step 6: 기능 회귀를 한 번 실행합니다.**

Run:

```bash
uv run --project backend pytest backend/tests -q
uv run --project backend ruff check backend
npm --prefix frontend test -- --run
npm --prefix frontend run typecheck
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:38100 npm --prefix frontend run build
npm --prefix frontend run e2e -- generic-analysis.spec.ts
npm --prefix frontend run e2e -- working-demo.spec.ts
```

Expected: Backend/Frontend 단위 테스트, typecheck, build, Fixture 기반 generic/legacy Playwright가 모두 통과합니다.

- [ ] **Step 7: 새 `.env`를 읽도록 서버를 재시작하고 실제 Gemini smoke를 실행합니다.**

먼저 기존 `33100/38100` 개발 서버만 종료합니다. Docker가 사용하는 `3100`은 건드리지 않습니다. 새 터미널에서 다음 명령을 실행합니다.

```bash
BACKEND_PORT=38100 FRONTEND_PORT=33100 make dev-gemini
```

Uvicorn 시작 로그에 다음 경로가 포함돼야 합니다.

```text
--env-file /Users/jin/hackerthon/demo-1/.env
```

다른 터미널에서 실제 Gemini smoke를 실행합니다.

```bash
LIVE_GEMINI_E2E=1 PLAYWRIGHT_REUSE_SERVER=1 \
  E2E_BACKEND_PORT=38100 E2E_FRONTEND_PORT=33100 \
  npm --prefix frontend run e2e -- \
  live-gemini-planner.spec.ts --project desktop-chromium
```

Expected: `gemini-3.7-flash`를 우선 사용해 Run이 완료됩니다.
Trace/Workspace/Artifact에 서로 다른 Primitive, Source, 공개 이유, Fact, 다음 행동이 남습니다.
`.env`의 LangSmith 설정은 새 Uvicorn 프로세스에 로드되며 Key 값은 로그나 문서에 기록하지 않습니다.

- [ ] **Step 8: 실제 관측 결과를 검증 문서에 기록합니다.**

`docs/verification/live-gemini-smoke.md`에 다음 항목만 추가합니다.

- 실행 시각과 사용 model
- 질문 원문
- 생성된 Plan의 revision과 Primitive 순서
- 사용 Source 목록
- 각 Step의 검증 Fact 요약
- Artifact JSON/Markdown 경로
- LangSmith tracing 설정 로드 여부
- 실패했다면 공개 error code와 마지막 검증 Step

API Key, LangSmith Key, Provider 원문, 비공개 추론은 기록하지 않습니다.

- [ ] **Step 9: 최종 기능 변경을 커밋합니다.**

```bash
git add \
  backend/src/customer_signal/api.py \
  backend/tests/test_api.py \
  backend/tests/test_runtime_generic.py \
  frontend/e2e/live-gemini-planner.spec.ts \
  frontend/e2e/generic-analysis.spec.ts \
  docs/verification/live-gemini-smoke.md
git commit -m "test: (demo) 동적 Planner E2E 검증"
```

## 완료 확인

- 자유 질문이 세 Scenario 중 하나로 바뀌지 않습니다.
- Gemini가 공개 Source와 10개 Primitive 계약을 보고 최초 Plan을 만듭니다.
- 잘못된 최초 Plan은 한 번만 재작성되고, 두 번째 실패에서는 Primitive를 실행하지 않습니다.
- 검증 Fact 뒤의 Continue/Stop/Revise 선택이 실제 실행과 공개 Note에 일치합니다.
- 화면에 Step 선택 이유, 구조화 조건, 사용 Source, 처리 통계, 검증 Claim, 다음 행동이 보입니다.
- 재시작 뒤에도 Artifact의 `plan_history`, `facts`, `notes`가 복원됩니다.
- JSON과 Markdown 다운로드에 최초 Plan, revision, Fact와 다음 행동이 남습니다.
- Fixture generic E2E와 기존 Legacy Journey E2E가 계속 통과합니다.
- `.env`를 읽은 새 서버에서 실제 Gemini 대표 질문 한 건이 완료됩니다.
