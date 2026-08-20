# Gemini 실호출 검증 기록

- 문서 상태: 완료
- 실행일: 2026-08-20
- 실행 환경: 로컬 합성 데이터
- Agent 모드: `gemini`
- 기본 모델: `gemini-3.7-flash`
- 대체 모델: `gemini-3.6-flash`

새 Uvicorn 프로세스가 main checkout의 `.env`를 `--env-file`로 읽은 뒤 세 질문을 실행했습니다. API Key, Provider 원문, 내부 추론은 터미널 출력과 Artifact에 기록하지 않았습니다.

## 환경 확인

- `GEMINI_API_KEY`: 설정 확인
- `LANGSMITH_API_KEY`: 설정 확인
- `LANGSMITH_TRACING`: 활성화 확인
- `LANGSMITH_PROJECT`: 설정 확인
- `LANGSMITH_ENDPOINT`: 설정 확인
- LangSmith 최근 LLM Trace: `gemini-3.7-flash`, 완료, 오류 없음

Frontend 프로세스에는 Gemini 또는 LangSmith 값을 전달하지 않았습니다.

## 실행 입력과 결과

공통 기간은 `[2026-07-20T00:00:00+09:00, 2026-08-19T00:00:00+09:00)`입니다. `search_history`, `search_feedback`, `digital_behavior`, `subscription`, `voc`를 사용했습니다.

| 질문 | Run ID | 상태 | 대표 Metric | Fact | Analysis Note |
| --- | --- | --- | --- | ---: | ---: |
| 최근 부정 피드백이 많은 Topic과 관련 고객 Segment를 알려줘. | `dd305a95-24df-4ad0-b0eb-82074be04346` | `completed` | `negative_feedback_customer_count = 6` | 3 | 3 |
| 같은 문제를 반복해서 찾은 뒤 고객센터까지 이동한 고객 흐름을 분석해 줘. | `1d05d800-154d-41eb-99af-5732e37679a2` | `completed` | `matched_customer_count = 6` | 4 | 4 |
| 가입 시작 뒤 완료하지 못한 고객과 이탈 단계를 알려줘. | `08b76386-b5a4-43f2-8ffb-ce8b5f40272e` | `completed` | `abandoned_customer_count = 5` | 3 | 3 |

Gemini는 질문을 세 가지 지원 시나리오 중 하나로 분류합니다. Plan, Fact,
Analysis Note, 최종 수치는 서버가 검증한 Primitive 결과로 작성합니다. 화면에는
내부 chain-of-thought 대신 Goal, Plan, 단계 상태, Fact Metric, 검증된 Analysis
Note를 공개합니다.

## 기록 확인

대표 Run `1d05d800-154d-41eb-99af-5732e37679a2`에서 아래 요청이 모두 `200`을 반환했습니다.

- `GET /api/run-artifacts/{run_id}`
- `GET /api/run-artifacts/{run_id}/document`
- `GET /api/run-artifacts/{run_id}/download.json`
- `GET /api/run-artifacts/{run_id}/download.md`
- `GET /api/run-artifacts`의 History 조회

Fixture E2E는 desktop과 mobile에서 세 질문의 `6/6/5`, 단계 표시, History
복원, JSON/Markdown 다운로드, 가로 overflow 부재를 확인했습니다. Generic E2E는
10건 통과와 의도한 2건 skip, 기존 Journey E2E는 4건 통과입니다.

## 판정

| 항목 | 결과 |
| --- | --- |
| Gemini 실호출 | 통과 |
| 세 질문 결과 | `6/6/5` |
| 단계별 공개 기록 | 통과 |
| JSON/Markdown 영속 기록 | 통과 |
| LangSmith Trace | 통과 |
| Blocker | 없음 |
