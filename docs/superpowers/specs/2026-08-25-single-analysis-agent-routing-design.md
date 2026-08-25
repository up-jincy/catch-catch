# 단일 Analysis Agent 라우팅 설계

작성일: 2026-08-25

상태: 구현 승인 대기

## 목표

모든 사용자 질문을 하나의 범용 `Analysis Loop`로 실행합니다. 질문 문구에 따라
Generic과 기존 Journey DeepAgent가 암묵적으로 바뀌는 현재 분기를 제거해, 화면과
Langfuse에서 항상 같은 실행 단계와 계약을 보여줍니다.

사용자와 팀원에게는 내부 구현명인 Generic이나 Legacy를 노출하지 않고 하나의
`Analysis Agent`로 설명합니다. 모델 실행 Provider는 별도 축으로 유지하며
`gemini`, `fixture`, `auto` 중 하나를 선택합니다.

## 배경

첫 번째 MVP는 `검색 실패 → 재검색 → 고객센터 문의` 한 가지 Journey를
DeepAgent와 MCP Tool로 분석했습니다. 이후 여러 자연어 목표를 지원하기 위해
Goal, Plan, Primitive, Fact, Note, Report 계약을 가진 범용 Analysis Loop를
추가했습니다. 기존 수직 Slice의 회귀를 피하려고 두 경로를 함께 유지했지만,
현재는 같은 화면에서 질문 표현에 따라 서로 다른 Trace와 결과 계약이 선택됩니다.

이 이중 라우팅은 최종 제품 기능이 아니라 단계적 확장 과정에서 생긴 호환 구조입니다.

## 결정

`POST /api/runs`는 질문 내용이나 `mode` 파라미터 유무와 관계없이 항상 Generic
Analysis Loop를 실행합니다.

```text
사용자 질문
  → AnalysisGoal
  → AnalysisPlan
  → 제한된 Analytics Primitive 실행
  → 검증된 Fact와 AnalysisNote
  → 검증된 Report
```

`mode`는 실행 구조를 선택하지 않습니다. 다음처럼 Provider만 선택합니다.

- `gemini`: 실제 Gemini 구조화 호출
- `fixture`: 결정론적 데모 모델
- `auto`: 서버 설정에 따라 사용 가능한 Provider 선택

기존 Journey 질문도 범용 Primitive 조합으로 처리합니다. Langfuse에는 하나의
`customer_signal.turn` 아래 `goal`, `plan`, Tool, `note`, `selection`, `report`
단계를 기록합니다.

## 변경 범위

### API 라우팅

- 질문 정규식에 기반한 Generic/Legacy 분기를 제거합니다.
- `create_run(..., generic=True, mode=selected_mode)`를 단일 진입점으로 사용합니다.
- `mode`가 없으면 서버의 기본 Provider 설정을 사용합니다.

### 호환성

- 기존 DeepAgent runner, MCP Tool과 Legacy 도메인 타입은 즉시 삭제하지 않습니다.
- 새 API Run에서는 Legacy runner를 호출하지 않습니다.
- 과거 Legacy Artifact의 조회, 문서 렌더링과 상세 Evidence 계약은 유지합니다.
- Legacy 코드는 신규 기능이 아니라 과거 기록 호환과 비교 테스트 대상으로만 둡니다.

### 화면과 관측성

- 화면은 기존 Generic Goal, Plan, Fact, Note, Report 흐름을 그대로 사용합니다.
- 사용자에게 Generic/DeepAgent 선택 UI를 추가하지 않습니다.
- 모든 신규 Langfuse Trace의 `run_kind`는 `generic`이며, Provider는 별도 metadata로
  확인합니다.

## 제외 범위

- DeepAgent와 Legacy 계약 전체 삭제
- MCP 서버와 기존 Tool 구현 삭제
- 과거 Artifact 데이터 마이그레이션
- Gemini 모델이나 Prompt 변경
- Frontend 레이아웃 변경
- 자유 SQL, 코드 실행, Subagent 추가

## 오류 처리

- Gemini 또는 Primitive 오류는 Generic Run의 기존 실패 계약으로 기록합니다.
- `auto` 실행 중 Provider 오류를 Legacy 또는 Fixture 결과로 몰래 전환하지 않습니다.
- 과거 Legacy Artifact를 읽는 과정에서 발생하는 오류는 기존 복원 규칙을 유지합니다.

## 검증

다음 조건을 모두 만족해야 합니다.

1. 기존 Journey 질문을 `mode` 없이 보내도 `run_kind="generic"`으로 생성됩니다.
2. 다른 범용 질문도 동일한 실행 경로를 사용합니다.
3. `mode=gemini`와 `mode=fixture`는 실행 구조가 아니라 Provider만 바꿉니다.
4. 기존 Journey 질문이 범용 Goal, Plan, Fact와 Report를 생성합니다.
5. 신규 Trace는 `customer_signal.turn` 한 개와 범용 stage 자식으로 구성됩니다.
6. 저장된 Legacy Artifact 조회와 렌더링 회귀 테스트는 계속 통과합니다.
7. Backend 전체 테스트와 Ruff 검사가 통과합니다.

## 롤백

변경은 API 진입점과 관련 테스트에 한정합니다. 문제가 생기면 단일 라우팅 커밋을
되돌려 기존 질문 정규식 분기를 복원할 수 있습니다. Legacy runner와 계약을 이번
변경에서 삭제하지 않으므로 데이터 복구나 역마이그레이션은 필요하지 않습니다.
