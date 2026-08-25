# Gemini 실호출 검증 기록

- 문서 상태: 완료
- 최근 실행일: 2026-08-25
- 실행 환경: 로컬 합성 데이터
- Agent 모드: `gemini`
- 기본 모델: `gemini-3.7-flash`
- 대체 모델: `gemini-3.6-flash`

Backend는 `uv run --env-file .env --project backend`로 시작해 Python import 전에
LangSmith와 Gemini 환경을 적용했습니다. API Key, Provider 원문, 내부 추론은
터미널 출력과 Artifact에 기록하지 않았습니다.

## 환경 확인

- `GEMINI_API_KEY`: 설정 확인
- `LANGSMITH_API_KEY`: 설정 확인
- `LANGSMITH_TRACING`: 활성화 확인
- `LANGSMITH_PROJECT`: 설정 확인
- `LANGSMITH_ENDPOINT`: 설정 확인
- `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL`: 설정 확인
- Langfuse SDK 인증: 성공
- LangSmith 최근 단계 Trace: `customer_signal.goal`, `plan`, `note`,
  `selection`, `report` 모두 완료, 오류 없음

Frontend 프로세스에는 Gemini 또는 LangSmith 값을 전달하지 않았습니다.

## 실행 입력과 결과

공통 기간은 `[2026-07-20T00:00:00+09:00, 2026-08-19T00:00:00+09:00)`입니다. `search_history`, `search_feedback`, `digital_behavior`, `subscription`, `voc`를 사용했습니다.

| 질문 | Run ID | 상태 | 대표 Metric | Fact | Analysis Note |
| --- | --- | --- | --- | ---: | ---: |
| 최근 부정 피드백이 많은 Topic과 관련 고객 Segment를 알려줘. | `dd305a95-24df-4ad0-b0eb-82074be04346` | `completed` | `negative_feedback_customer_count = 6` | 3 | 3 |
| 같은 문제를 반복해서 찾은 뒤 고객센터까지 이동한 고객 흐름을 분석해 줘. | `1d05d800-154d-41eb-99af-5732e37679a2` | `completed` | `matched_customer_count = 6` | 4 | 4 |
| 가입 시작 뒤 완료하지 못한 고객과 이탈 단계를 알려줘. | `08b76386-b5a4-43f2-8ffb-ce8b5f40272e` | `completed` | `abandoned_customer_count = 5` | 3 | 3 |

위 표는 기존 대표 시나리오의 회귀 기준입니다.

### 자유 질문 동적 Plan

2026-08-21에는 아래 자유 질문을 실제 Gemini 모드로 실행했습니다.

> 최근 부정적인 피드백을 남긴 고객은 이후 어떤 행동 패턴을 보이고, 일반 고객과 무엇이 달라?

- Run ID: `2f2449a6-2e36-47eb-b5a2-7d769e3fcde7`
- 상태: `completed`
- Plan revision: `0`
- 실행 Primitive: `catalog_sources` → `profile_events` → `profile_events` →
  `compare_segments`
- 공개 기록: Fact 4개, Analysis Note 4개, 최종 보고서 1개
- 브라우저 실검증: 41.1초, desktop Chromium 통과

Gemini는 공개 Source Manifest와 10개 읽기 전용 Primitive 계약을 보고 Goal과
Plan을 수립합니다. 서버는 각 실행 결과를 Fact로 고정하고 Claim을 Fact에 다시
결합한 뒤에만 Analysis Note와 최종 보고서를 공개합니다. 화면에는 내부
chain-of-thought 대신 Plan 선택 근거, 검증 Fact, 관찰 Fact, 다음 행동을 표시합니다.

## 기록 확인

동적 Plan Run `2f2449a6-2e36-47eb-b5a2-7d769e3fcde7`에서 아래 요청이 모두
`200`을 반환했습니다.

- `GET /api/run-artifacts/{run_id}`
- `GET /api/run-artifacts/{run_id}/document`
- `GET /api/run-artifacts/{run_id}/download.json`
- `GET /api/run-artifacts/{run_id}/download.md`
- `GET /api/run-artifacts`의 History 조회

Fixture E2E는 desktop과 mobile에서 세 질문의 `6/6/5`, 단계 표시, History
복원, JSON/Markdown 다운로드, 가로 overflow 부재를 확인했습니다. Generic E2E는
10건 통과와 의도한 2건 skip, 기존 Journey E2E는 4건 통과입니다.
실제 Gemini E2E는 자유 질문의 동적 Plan, 선택 근거, Fact, Analysis Note,
최종 문서와 History 복원을 확인했습니다.

## Langfuse 단일 Turn Trace 검증

2026-08-25에는 Compass의 Turn 관측 패턴처럼 API Run 하나를 Trace 하나로 묶는 구성을
실제 Langfuse에서 확인했습니다.

| 실행 경로 | Run ID | API 상태 | Langfuse 결과 |
| --- | --- | --- | --- |
| 범용 단계형 Gemini | `b7c8f271-bb92-40fd-bacf-158149522ad2` | `completed`, Fact 3개, Note 3개 | Trace 1개, `customer_signal.turn` |
| 기존 Journey DeepAgent | `a6fe3792-f1f6-4117-a68a-ae8780ffec62` | `completed`, 보고서 생성 | Trace 1개, `customer_signal.turn` |

범용 Trace의 부모 Turn 아래에는 `goal`, `plan`, `catalog_sources`,
`match_sequence`, `get_customer_journey`, `note`, `selection`, `report`가 같은 트리로
연결됐습니다. DeepAgent Trace에서는 `customer_signal.agent`가 Turn의 직접 자식이고,
그 아래 Todo, 모델, Tool 관측 61개가 연결됐습니다. 부모 Turn과 Agent 관측에는 모두
입력과 출력이 있었습니다.

Gemini Key, Langfuse Secret Key, 테스트 이메일, `private reasoning`,
`provider transcript` 문자열이 Trace payload에 없는 것도 확인했습니다. Langfuse
적재 실패가 분석 결과를 실패시키지 않는 fail-open 테스트도 통과했습니다.

## 판정

| 항목 | 결과 |
| --- | --- |
| Gemini 실호출 | 통과 |
| 세 질문 결과 | `6/6/5` |
| 자유 질문 동적 Plan | 통과, Fact 4개와 Analysis Note 4개 |
| 단계별 공개 기록 | 통과 |
| JSON/Markdown 영속 기록 | 통과 |
| LangSmith Trace | 통과 |
| Langfuse 단일 Turn Trace | Generic·DeepAgent 모두 Trace 1개로 통과 |
| Langfuse 입력·출력과 Tool 흐름 | 통과 |
| Secret·비공개 message 미기록 | 통과 |
| Blocker | 없음 |
