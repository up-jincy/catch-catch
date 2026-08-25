# Langfuse 콜백 관측 설계

작성일: 2026-08-25

## 목표

Gemini로 실행한 고객 신호 분석을 Langfuse에서 재생할 수 있게 합니다. 개발자는 한
Run에서 사용자 발화, 모델이 만든 목표와 계획, 사용 가능한 Tool, 실제 선택한 Tool,
Tool 입력과 검증된 출력, 최종 보고서를 확인할 수 있어야 합니다.

이 작업은 프로토타입 검증이 목적이므로 LangChain과 DeepAgents 호출부에 Langfuse
콜백을 직접 전달합니다. 별도 관측 프레임워크나 범용 추상화는 만들지 않습니다.

## 적용 범위

다음 두 Gemini 실행 경로를 모두 추적합니다.

- 범용 단계형 분석: `goal`, `plan`, `note`, `selection`, `report`
- 기존 DeepAgent Journey 분석: Agent 계획, MCP Tool 호출, 구조화된 최종 응답

Fixture 모드는 외부 모델을 호출하지 않으므로 Langfuse LLM Trace를 만들지 않습니다.

## Langfuse에서 보이는 정보

| 단계 | 입력 | 출력 |
| --- | --- | --- |
| API Run | 사용자 발화, 기간, 선택 Source | Run 상태와 공개 Run ID |
| `goal` | 발화, Source manifest, Primitive catalog | 검증된 `AnalysisGoal` |
| `plan` | Goal, 후보 Primitive와 제약 | 검증된 `AnalysisPlan`과 선택 이유 |
| Primitive | Primitive 이름, Source, 공개 parameters | 검증된 Fact, 지표, 스캔과 매칭 건수 |
| `note` | 현재 Step과 Fact | 검증된 `AnalysisNote` |
| `selection` | 완료 Step과 남은 Plan | 다음 Step 또는 종료 선택 |
| `report` | Goal, Plan, Fact, Note | 검증된 최종 보고서 |
| DeepAgent | 사용자 발화와 공개 실행 범위 | Todo 계획, MCP Tool 흐름, 검증된 보고서 |

각 관측에는 `run_id`, `run_kind`, `provider`, `stage`, 모델명, 선택 Source를 공개
metadata로 기록합니다. 범용 분석의 여러 모델 호출과 Primitive span은
`langfuse_session_id=run_id`로 묶습니다.

## 구현 방식

Backend에 `langfuse` SDK를 고정 버전으로 추가합니다. 환경변수
`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL`은 SDK가 직접
읽습니다.

공통 Helper는 다음 기능만 제공합니다.

- 요청별 공개 `run_id`와 Run 종류 보관
- `CallbackHandler` 생성
- 기존 `run_name`, tags, metadata에 Langfuse callback과 session metadata 추가
- 공개 Primitive span 생성
- 서버 종료 시 남은 이벤트 flush
- 설정 누락이나 적재 장애 시 분석을 계속하는 fail-open 처리

범용 Gemini는 기존 `chain.ainvoke(prompt, config=config)`의 config에 callback을
추가합니다. DeepAgent는 기존 `agent.ainvoke(state)`를
`agent.ainvoke(state, config=config)`로 바꿉니다. Analysis Loop는
`PrimitiveExecutor.execute_async` 호출을 공개 span으로 감쌉니다.

## 데이터 보호

Langfuse에는 합성 데이터와 공개 계약만 기록합니다. 다음 값은 기록하지 않습니다.

- Gemini, Langfuse, LangSmith API Key
- 원본 PII와 비공개 Source 원문
- Provider의 검증 전 원문
- DeepAgent의 비공개 추론과 내부 message state

클라이언트 측 export 마스킹은 secret 계열 필드와 비공개 Agent message를 제거합니다.
사용자 발화는 PII 패턴을 마스킹한 뒤 남깁니다. Tool 출력은 서버가 검증해 공개한
합성 결과만 남깁니다. 모델의 구조화된 초안은 서버 검증을 통과한 값으로 관측 출력을
갱신합니다.

## 환경 격리

선택한 `.env`의 `LANGFUSE_*` 값이 기존 shell 값보다 우선해야 합니다. Backend 실행
직전에 기존 `LANGFUSE_*` 값을 제거하고 `uv run --env-file`로 다시 전달합니다.
Frontend 프로세스에서는 `LANGFUSE_*` 값을 제거합니다. `.env`를 shell에서
`source`하지 않습니다.

## 오류 처리

Langfuse 설정이 없거나 서버가 응답하지 않아도 Gemini 분석은 계속합니다. 경고에는
오류 종류만 남기고 URL, Key, 요청 또는 응답 원문은 기록하지 않습니다. Gemini와
검증 오류 정책은 변경하지 않습니다.

## 테스트

구현 전 다음 실패 테스트를 먼저 추가합니다.

1. 범용 Gemini의 모든 단계가 callback, session, stage metadata를 전달하는 테스트
2. DeepAgent가 callback config와 Run metadata를 전달하는 테스트
3. Primitive 관측에 선택 Tool의 공개 입력과 검증된 출력이 남는 테스트
4. API Key, PII, 비공개 message가 export 전에 제거되는 테스트
5. Langfuse 설정 누락과 적재 오류가 분석 Run을 실패시키지 않는 테스트
6. Launcher가 Backend와 Frontend의 `LANGFUSE_*` 환경을 격리하는 테스트

구현 후 전체 Backend 테스트와 Ruff를 실행합니다. Gemini 서버를 새로 시작하고 승인된
합성 질문을 한 번 실행한 뒤 Langfuse에서 최신 Trace의 시간, `run_name`, session,
stage, Tool 입력과 검증된 출력을 확인합니다. Key 원문은 로그와 검증 기록에 남기지
않습니다.

## 완료 조건

- 범용 Gemini와 DeepAgent Run이 Langfuse에 적재
- 사용자 발화에서 최종 보고서까지 Run 단위 조회 가능
- 후보 Primitive와 실제 선택 Primitive 식별 가능
- 공개 Tool 입력과 검증된 출력 조회 가능
- Key, PII, Provider 원문, 비공개 추론 미기록
- Langfuse 장애 시 Gemini 분석 정상 동작
