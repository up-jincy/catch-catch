# Gemini 실호출 검증 기록

- 검증일: 2026-08-20
- 실행 기준: `30deb7b`
- 실행 모드: `gemini`
- 기본 모델: `gemini-3.7-flash`
- 결과: 완료

## 검증 범위

로컬 FastAPI와 FastMCP를 실제로 기동하고 `GEMINI_API_KEY`를 프로세스 환경에만
주입했습니다. 아래 질문을 HTTP Run API로 제출한 뒤 SSE 종료 이벤트와 공개
`InsightReport`를 확인했습니다.

```text
AI 검색에서 해결하지 못하고 고객센터에 문의한 고객이 몇 명이야?
```

- 기간: `2026-07-20T00:00:00+09:00` 이상,
  `2026-08-19T00:00:00+09:00` 미만
- Source: `search_history`, `search_feedback`, `digital_behavior`, `subscription`,
  `voc`
- Run ID: `884e2de6-1291-4be9-b73a-6473f2012261`
- 완료 시간: 45.4초

## 결과

| 항목 | 관측값 |
| --- | --- |
| Run 상태 | `completed` |
| Agent 모드 | `gemini` |
| Headline | `검색 실패 후 문의로 이어진 고객 6명` |
| Metric | `완전한 Journey 패턴 고객 수 = 6명` |
| 공개 Ranking | 6명 |
| Finding에 연결된 Evidence | 4건 |
| 공개 오류 | 없음 |

모델은 Journey Evidence 6건을 조회했습니다. 서버는 그중 Pattern의 대표 신호에도
속하는 4건만 Finding과 Recommendation에 연결했습니다.

## MCP Tool Trace

| 순서 | Tool | 반환 건수 |
| --- | --- | ---: |
| 1 | `catalog_sources` | 5 |
| 2 | `aggregate_events` | 4 |
| 3 | `match_journey_pattern` | 6 |
| 4 | `rank_customers` | 24 |
| 5 | `get_customer_journey` | 6 |
| 6 | `get_evidence` | 6 |

각 Tool은 한 번씩만 호출됐습니다. Tool 실행 뒤 `validating`, `result`, `done`
이벤트가 순서대로 도착했습니다.

## 실제 Agent 계획

Gemini가 Tool 실행 결과와 함께 반환한 Todo는 다음과 같습니다.

1. `Catalog sources in range`
2. `Aggregate events by topic`
3. `Match journey pattern`
4. `Rank customers`
5. `Fetch journey for customer CUST-003`
6. `Fetch evidence and generate final InsightReport JSON`

## 재현 방법

저장소 루트의 `.env`에 `GEMINI_API_KEY`를 설정한 뒤 다음 명령을 실행합니다.

```bash
make seed
make dev-gemini
```

브라우저에서 기본 질문과 5개 Source를 선택해 실행하면 같은 `6명` 계약을 확인할
수 있습니다. 합성 데이터는 고정 Seed를 사용하지만 Provider 응답 시간과 Todo 문구는
실행마다 달라질 수 있습니다.

## 보안 경계

- API Key, Provider 원문, 내부 추론은 이 문서와 공개 SSE에 기록하지 않았습니다.
- 모델 서술은 서버가 MCP 결과로 만든 정답 문구와 정확히 일치할 때만 공개합니다.
- 모델이 선택한 Evidence ID는 같은 Run에서 조회한 값인지 확인합니다.
- 공개 보고서는 서버가 Pattern 근거와 교차 검증한 Evidence ID만 사용합니다.

SDK는 실행 중 일부 JSON Schema 키를 무시한다는 경고를 출력했지만, 구조화 응답과
최종 검증은 정상 완료됐습니다.
