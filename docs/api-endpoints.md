# Backend API 엔드포인트 정리

Backend(FastAPI)가 노출하는 HTTP 엔드포인트 목록입니다. 이 문서는 사람이 읽는
요약이고, 기계가 읽는 원본 계약은 실행 중인 서버의 Swagger 문서입니다.

## Swagger 문서 위치

Backend 를 실행하면 FastAPI 가 다음 경로를 자동으로 제공합니다.

| 경로 | 내용 |
| --- | --- |
| `/docs` | Swagger UI |
| `/redoc` | ReDoc UI |
| `/openapi.json` | OpenAPI 3.1 스키마 원본 |

기본 로컬 주소는 `http://127.0.0.1:8000/docs` 입니다. 실제 포트는
`Settings.api_port` 설정을 따릅니다.

## system

| Method | 경로 | 설명 | 응답 |
| --- | --- | --- | --- |
| GET | `/health` | 서비스 상태 확인 | `{"status": "ok"}` |

## sources

| Method | 경로 | 설명 | 응답 |
| --- | --- | --- | --- |
| GET | `/api/sources` | 분석에 사용할 수 있는 공개 Source 목록 조회 | `PublicSourceList` |

## runs

| Method | 경로 | 설명 | 응답 |
| --- | --- | --- | --- |
| POST | `/api/runs` | 분석 Run 생성. `mode` 쿼리로 agent 모드 지정 가능 | `202` + `RunAccepted` |
| POST | `/api/runs/{run_id}/clarification` | Clarification 대기 중인 Run 에 답변 제출 | `202` + `RunAccepted` |
| GET | `/api/runs/{run_id}` | Run 상태 스냅샷 조회 | `RunSnapshot` |
| GET | `/api/runs/{run_id}/events` | Run 이벤트 SSE 스트림. `Last-Event-ID` 헤더로 재접속 커서 지정 | SSE |
| GET | `/api/runs/{run_id}/customers/{customer_id}/journey` | 완료된 Run 의 고객 Journey 조회 | `CustomerJourneyResult` |
| GET | `/api/runs/{run_id}/evidence/{evidence_id}` | 완료된 Run 의 마스킹 Evidence 조회 | `EvidenceResult` |

## run-artifacts

| Method | 경로 | 설명 | 응답 |
| --- | --- | --- | --- |
| GET | `/api/run-artifacts` | 저장된 Run Artifact 목록 조회 | `ArtifactListResponse` |
| GET | `/api/run-artifacts/{run_id}` | Run Artifact 단건 조회 | Artifact JSON |
| GET | `/api/run-artifacts/{run_id}/document` | Run 보고서 문서 렌더링 조회 | 문서 JSON |
| GET | `/api/run-artifacts/{run_id}/download.json` | Run Artifact JSON 파일 다운로드 | `application/json` |
| GET | `/api/run-artifacts/{run_id}/download.md` | Run 보고서 Markdown 파일 다운로드 | `text/markdown` |

## 공통 오류 응답

| 상태 코드 | 발생 조건 |
| --- | --- |
| `400` | `Last-Event-ID` 헤더가 정수가 아니거나 발행된 이벤트 범위를 벗어난 경우 |
| `404` | 존재하지 않는 Run, Run 리소스, Run Artifact 를 조회한 경우 |
| `409` | 완료되지 않은 Run 의 후속 리소스를 조회하거나, Clarification 대기 상태가 아닌 Run 에 답변한 경우 |
| `422` | `enabled_sources` 에 알 수 없는 Source 가 포함된 경우 |

## Swagger 밖의 경로

`/mcp` 에는 FastMCP 서버가 별도 ASGI 앱으로 mount 되어 있습니다. mount 된 앱은
FastAPI OpenAPI 스키마에 포함되지 않으므로 Swagger UI 에 나타나지 않습니다.
