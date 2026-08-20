# 동적 고객 신호 분석 Planner 설계

작성일: 2026-08-20

## 목표

Gemini가 공개된 Source 정보와 읽기 전용 Primitive를 보고 분석 Plan을 직접 구성합니다. 서버는 Plan과 Fact를 검증하고, 검증된 근거만 화면과 Run 문서에 공개합니다.

이번 변경은 프로토타입의 분석 유연성을 확인하는 데 집중합니다. 모든 질문 조합을 방어하는 프로덕션 수준의 검증 확대는 범위에 포함하지 않습니다.

## 사용자 경험

사용자는 자연어 질문을 제출하면 별도 승인 없이 분석을 시작합니다. 오른쪽 Trace에는 다음 내용을 실행 순서대로 표시합니다.

1. 모델이 해석한 분석 Goal
2. 사용 가능한 Source와 Primitive를 확인한 결과
3. 모델이 만든 최초 Plan과 각 Step의 선택 이유
4. 각 Step이 사용한 Source, 조건, 관찰한 Fact
5. 관찰 결과에 따른 다음 Step 선택 또는 Plan 수정 이유
6. 검증된 최종 보고서와 분석 한계

Trace는 모델의 비공개 사고과정을 노출하지 않습니다. 모델이 제출한 짧은 구조화 설명과 서버가 검증한 Fact를 연결한 공개 분석 기록을 표시합니다.

완료되거나 중단된 Run은 이전 Run 목록에서 다시 열 수 있어야 합니다. JSON과 Markdown 문서에는 최초 Plan, Plan 수정 이력, Fact, Analysis Note, 최종 상태를 기록합니다.

## 실행 흐름

```text
질문
  → Source와 Primitive 탐색
  → Gemini Goal 생성
  → Gemini 최초 Plan 생성
  → 서버 Plan 검증
  → Step 실행
  → 서버 Fact 검증과 공개
  → Gemini Analysis Note와 다음 Step 선택
  → 필요하면 미완료 Plan 수정과 재검증
  → 최종 보고서 검증과 Artifact 저장
```

### 1. 탐색

서버는 모델에게 원천 컬럼이나 원본 Event를 주지 않습니다. 다음 공개 정보만 전달합니다.

- Source ID, 설명, 기간, Event 수
- Source가 공개한 Dimension과 Measure
- 10개 읽기 전용 Primitive의 이름, 역할, 입력과 출력 스키마
- 요청 기간과 사용자가 선택한 Source

Source catalog 조회는 분석 Plan을 만들기 위한 탐색으로 취급합니다. 이 조회는 쓰기 작업이나 임의 SQL을 허용하지 않습니다.

### 2. Goal과 최초 Plan

Gemini는 자유 질문을 세 가지 고정 Scenario 중 하나로 치환하지 않습니다. 질문, Source catalog, Primitive 스키마로 `AnalysisGoal`과 3~6개의 `AnalysisStep`을 생성합니다.

각 Step은 다음 정보를 포함합니다.

- 사용할 Primitive와 Source
- 앞선 Fact 의존성
- 구조화된 조회 조건
- 기대 출력과 종료 조건
- 사용자에게 공개할 짧은 선택 이유

서버는 기존 `PlanValidator`로 Source, Capability, PII, 의존성, 실행 범위와 한도를 검증합니다.
검증에 실패하면 오류 요약을 Gemini에 한 번 전달해 Plan을 다시 생성합니다.
두 번째 Plan도 실패하면 실행하지 않고 실패 Artifact를 남깁니다.

### 3. Fact 기반 실행과 Plan 수정

서버는 검증된 Plan의 Step을 한 번에 하나씩 실행합니다. Primitive 결과는 기존 `PrimitiveExecutor`와 Fact 계약을 거쳐야 공개됩니다.

Gemini는 각 Fact를 받은 뒤 다음 중 하나를 선택합니다.

- 검증된 다음 Step 계속 실행
- 분석 목표를 달성해 종료
- 완료된 Step은 유지하고 미완료 Step만 수정

수정된 Plan도 실행 전에 서버 검증을 통과해야 합니다. 전체 Plan은 6개 Step과 기존 Run 시간, 행 수, Evidence 한도를 넘지 않습니다.

### 4. 공개 Analysis Note

각 Step이 끝나면 Gemini는 다음 필드로 공개 Note를 제출합니다.

- `selection_reason`: 이 Primitive를 선택한 이유
- `observed_facts`: 서버가 검증한 Metric과 Fact 참조
- `interpretation`: Fact 범위 안의 짧은 해석
- `next_action`: 다음 Step 또는 종료 이유
- `limitations`: 데이터와 분석의 한계

서버는 Fact에 없는 수치, Source, 고객, Evidence를 제거하거나 거부합니다. 화면과 문서에는 검증을 통과한 Note만 표시합니다.

### 5. 최종 보고서

Gemini는 검증된 Goal, 최종 Plan, Fact와 Note로 보고서 초안을 만듭니다. 서버는 기존 Claim 검증과 문서 Composer를 사용해 최종 문서를 생성합니다.

질문이 여러 Source의 행동 패턴을 요구하면 보고서는 실제로 사용한 Source별 Fact와 비교 결과를 포함해야 합니다.
고객 수만 계산한 결과를 행동 패턴 분석으로 표시하지 않습니다.

## 구성 요소 변경

### `GeminiAnalysisModel`

현재 `GenericFixtureModel`에 위임하는 `create_plan`, `create_note`, `select_next`, `create_report`를 Gemini 구조화 호출로 교체합니다.
Fixture 모델은 테스트와 명시적인 Fixture 실행 모드에만 남깁니다.

### `AnalysisLoop`

Source 탐색 결과를 Plan 생성 Context에 포함합니다. Plan 검증 실패 시 한 번의 수정 기회를 제공하고, 실행 중 `plan_revised` 이벤트를 기존 스트림으로 공개합니다.

### Runtime과 Artifact

Run 이벤트와 Artifact에 최초 Plan, Plan revision, 공개 선택 이유, Fact, Note를 순서대로 저장합니다. 실패 Run도 마지막으로 검증된 Fact와 실패 Step을 보존합니다.

### Frontend

기존 Goal, Plan, Fact, Analysis Note 영역을 재사용합니다. Trace와 Plan 카드에 다음 항목을 추가합니다.

- Step 선택 이유
- 실제 Source와 조건
- 관찰한 핵심 Fact
- 다음 Step 또는 Plan 수정 이유
- Plan revision 표시

## 오류 처리

- Gemini 호출 실패: 공개 오류와 마지막 검증 상태를 저장하고 Run 종료
- 최초 Plan 검증 실패: 오류 요약으로 한 번 재작성한 뒤 실패 처리
- 실행 중 Plan 수정 실패: 기존 Plan과 Fact를 보존하고 실패 처리
- 데이터 없음: 확인한 Source와 범위를 포함한 `degraded` 문서 생성
- Primitive 실패: 실패 Step과 이전 Fact를 포함한 부분 Artifact 생성
- 사용자 취소 또는 시간 초과: 새 Tool 실행 중단과 현재 상태 저장

Gemini 모드의 실패를 고정 Fixture Plan의 성공으로 바꾸지 않습니다. Fixture로 실행한 경우에는 화면과 Artifact의 `agent_mode`에 이를 표시합니다.

## 기능 검증

검증은 사용자가 확인할 수 있는 동작에 집중합니다.

1. 자유 질문이 고정 Scenario 치환 없이 Goal과 Plan으로 변환되는지 확인
2. 행동 패턴 질문이 2개 이상의 분석 Primitive와 여러 Source를 선택하는지 확인
3. 첫 Fact에 따라 다음 Step 또는 미완료 Plan이 달라지는지 확인
4. Trace에 선택 이유, Fact, 다음 단계가 순서대로 나타나는지 확인
5. 완료 Run의 JSON과 Markdown에 Plan revision과 근거가 남는지 확인
6. 잘못된 Plan이 Tool 실행 전에 거부되고 실패 문서가 남는지 확인
7. 기존 Fixture E2E가 명시적인 Fixture 모드에서 계속 동작하는지 확인

실제 Gemini E2E는 대표 자유 질문 한 건을 기준으로 수행합니다. 나머지 계약 검증은 결정론적 Fake 모델로 실행합니다.

## 제외 범위

- 자유 SQL과 원본 데이터 직접 조회
- 모델의 비공개 사고과정 공개
- 사용자의 단계별 승인
- 장기 기억과 여러 Run 사이의 자동 학습
- 모든 질문과 Tool 조합의 공격적 정합성 검증
- 새로운 분석 Primitive와 외부 Source 추가

## 완료 기준

대표 자유 질문 한 건이 Gemini 모드에서 Source와 Primitive를 탐색해 고정 템플릿과 다른 Plan을 구성해야 합니다.
실행 중 Fact에 따라 다음 단계를 선택해야 합니다.
사용자는 오른쪽 Trace와 저장된 Markdown 문서에서 선택 이유, 사용 데이터, 관찰 결과와 최종 판단을 확인할 수 있어야 합니다.
