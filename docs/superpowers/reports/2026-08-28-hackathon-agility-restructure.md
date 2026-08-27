# 해커톤 기민성 구조 정리 리포트

작성일: 2026-08-28

상태: 구현 완료, 전체 테스트 통과

## 목표

해커톤 중에 자주 생기는 세 가지 변화를 싼 변경으로 만드는 것이 목표였습니다.

- 새 데이터 원천 붙이기
- 표현 구조 바꾸기
- 분석 방식(어댑터) 여러 개 붙이기

그리고 흩어져 있던 실행 기록을 하나의 정본으로 정리하는 것이 네 번째 목표였습니다.
설계 기준은 [2026-08-27 Analysis Pack, A2UI 구조 설계](../specs/2026-08-27-analysis-pack-a2ui-frontend-design.md)입니다.

## 무엇을 했나

여섯 개의 태스크를 순서대로 커밋했습니다. 각 태스크는 독립적으로 검증했습니다.

| 커밋 | 내용 | 새 경계 |
| --- | --- | --- |
| `a6297db` | Canonical Run Event와 EventJournal 이중 Adapter | 데이터 정본 경계 |
| `4cfa3e4` | AnalysisPackAdapter 계약, Registry, Pack Kernel | 분석 어댑터 경계 |
| `28e48fb` | CustomerSignalPack 전환과 EventJournal 정본화 | 실행 경로 통합 |
| `6975707` | Primitive 정의 단일 카탈로그 통합 | 분석 어휘 경계 |
| `f5d1419` | PresentationIntent 투영 심과 replay 엔드포인트 | 표현 경계 |
| `bc7c06a` | AnalysisPackHarness와 두 번째 Pack 검증 | 경계 검증 장치 |

검증 결과는 Backend pytest 587건, Frontend vitest 95건, ruff, tsc, next build 전부 통과입니다.

구현 뒤 멀티에이전트 적대적 리뷰(리뷰어 4, 검증자 18)를 실행해 18건의 지적 중
14건을 실제 결함으로 확인했고, 이 중 동시성과 복원 관련 결함을 후속 커밋에서
수정했습니다. 수정 내역은 아래 리뷰 반영 절을 참고합니다.

## 결론: 어디가 갈라졌고 무엇이 싸졌나

### 1. 데이터 원천이 하나로 정리됐습니다

Run에서 일어난 모든 일은 이제 append-only Canonical Run Event로
`EventJournal`(SQLite)에 먼저 커밋됩니다. 저널에 커밋되지 않은 것은 외부로 나가지 않습니다.

- SSE 스트림: 저널 event를 `wire_projection`이 기존 어휘로 투영한 파생물
- Run 스냅샷과 Artifact JSON/Markdown: 같은 event의 파생 캐시
- Backend 재시작 뒤 SSE cursor replay: 이전에는 불가능했고 지금은 저널 replay로 복원

파생물이 손상되면 버리고 저널에서 다시 만들면 됩니다. 이것이 피벗의 안전판입니다.

### 2. 분석 방식은 Pack 하나로 추가합니다

새 분석의 중앙 변경은 Pack 모듈 파일 하나와 composition root의 Registry 한 줄입니다.
`SourceOverviewPack`이 증거입니다. 자체 Goal, Plan, Fact, Report schema를 갖고,
Frontend와 공용 API 계약을 건드리지 않고 Kernel 위에서 실행됩니다.

Pack이 지켜야 하는 규칙(방출 순서, 터미널 1개, 공개 안전성, 결정론, 프로젝터 순수성)은
`AnalysisPackHarness` 한 번 호출로 검증합니다. 해커톤 중 "분석 하나 더"의 검증 비용이
테스트 함수 하나로 줄었습니다.

### 3. Primitive 어휘가 한 곳에 모였습니다

이전에는 Primitive 하나를 추가하면 backend 6곳과 frontend 1곳 이상을 기억해서
고쳐야 했습니다. 지금은 `domain/primitive_catalog.py`의 정의 1건과 핸들러 등록 1건이
중앙 변경이고, 나머지(arity 표 3곳, planner prompt 표, objective 라벨, capability 집합)는
전부 파생입니다. 빠뜨리면 import 시점 fail-fast 또는
`contracts/primitive-catalog.json` drift 테스트가 실패합니다.

### 4. 표현은 투영이고, 투영은 순수 함수입니다

`PackProjector`는 Canonical Run Event를 protocol 중립 `PresentationIntent`로 접는
순수 함수입니다. 같은 event log는 언제나 같은 Intent를 만듭니다.
`GET /api/runs/{run_id}/presentation`이 이를 저널에서 재계산해 보여 줍니다.
표현을 바꾸고 싶으면 프로젝터를 교체하면 되고, 분석 코드는 건드리지 않습니다.

## 수평확장 포인트

| 확장 | 변경 지점 | 검증 방법 |
| --- | --- | --- |
| 새 데이터 Source | `data/onboarded-sources`에 spec과 파일 등록 | 기존 source contract suite |
| 새 분석 Pack | Pack 모듈 1개 + Registry 1줄 | `assert_pack_contract` 하니스 |
| 새 Primitive | 카탈로그 정의 1건 + 핸들러 1건 | catalog drift 테스트 |
| 새 표현 | 프로젝터 교체 또는 Pack별 override | 순수성/동등성 테스트 |
| 새 저장 백엔드 | `EventJournal` Adapter 1개 | 공용 journal contract suite 27건 |
| 모델 provider 교체 | `AnalysisModel` 구현 1개 | AnalysisLoop 기존 테스트 |

구체적인 절차는 [해커톤 기민성 런북](../../hackathon-agility-runbook.md)에 있습니다.

## 피벗 시나리오

- **분석 도메인 피벗**: 기존 CustomerSignalPack을 두고 새 Pack을 등록해 병행 운영합니다.
  Canonical Run Event envelope는 Pack이 늘어도 변하지 않으므로 저널, Kernel, 표현
  파이프라인은 재사용됩니다.
- **UI 전면 교체 (A2UI, CopilotKit)**: 설계 문서의 3~4단계입니다. PresentationIntent가
  이미 protocol 중립이므로, A2UIProjectionAdapter는 Intent를 A2UI message로 encode하는
  Adapter 하나로 추가합니다. 현재 SSE 계약은 `wire_projection` 한 파일에 격리되어 있어
  새 Host가 검증될 때까지 병행 유지가 쉽습니다.
- **저장소 피벗 (Postgres 등)**: `EventJournal` Protocol 구현 하나를 추가하고 journal
  contract suite를 통과시키면 됩니다. 호출부는 composition root 한 줄입니다.
- **실데이터 연결 피벗**: SourceRegistry Adapter 경계가 그대로이므로, 합성 DuckDB
  Adapter 옆에 실데이터 Adapter를 추가 등록하면 됩니다. evidence masking과 identity
  규칙은 Registry가 소유하므로 Adapter는 읽기만 구현합니다.

## 리뷰 반영

적대적 리뷰가 확인한 결함과 조치입니다.

| 확인된 결함 | 조치 |
| --- | --- |
| 취소된 SQLite append가 트랜잭션을 고아화해 저널 손상 가능 | 락을 잡은 채 worker thread 완료를 기다리는 `_write`로 수정 |
| 재시작 시 저널 터미널과 스냅샷 상태 불일치(좀비 running Run) | 복원 시 저널 event fold로 상태 재구성, 터미널 없는 Run은 `run_interrupted` 처리 |
| 아티팩트 없는 저널 Run 복원 불가 | `run.opened`의 공개 입력으로 RunStore에 재등록 |
| Kernel의 open/resume 구간이 터미널 보장 밖 | 해당 구간을 예외 정규화 try 안으로 이동 |
| 터미널 커밋 중 취소 시 저널과 상태 모순 | 진행 중 터미널 커밋은 완료 후 결과 반환 |
| tail()이 터미널 지난 cursor에서 무한 폴링 | 터미널 도달 검사 후 종료 |
| CAS 재시도가 일반 append에서 last-writer-wins로 변질 | 재시도를 터미널 커밋에만 허용 |
| `PackDegraded(())`가 정규화 밖에서 kernel 크래시 | 기본 제한 문구로 대체 |
| 실패 경로에서 Pack outcome 메모리 잔류 | 실패 경로에서 outcome drain |
| document_renderer의 Primitive 라벨 표 드리프트 무감지 | 카탈로그 동기화 테스트 추가 |

수정하지 않고 기록만 남긴 확인 사항은 다음과 같습니다.

- `OutcomeDraft`는 설계의 6종 방출에 없는 확장입니다. Pack이 도메인 결과를 선언하는
  통로로 유지하되 lifecycle event 생성은 Kernel이 소유합니다.
- presentation 엔드포인트는 저널 커밋 시점의 공개 검증(deny-list)에 의존합니다.
  A2UI Adapter를 붙일 때 protocol schema 검증이 추가됩니다.
- 하니스는 설계가 요구한 timeout과 cancellation 주입 검사를 아직 포함하지 않습니다.

## 남은 경계 부채

정직한 현황입니다. 다음은 아직 남아 있고, 설계 문서의 5단계(Legacy 제거)에 해당합니다.

- `POST /api/runs`는 여전히 customer_signal 전용입니다. 다른 Pack의 Run 생성 API는
  Kernel 직접 호출로만 가능합니다.
- Frontend는 아직 domain decoder와 Goal/Plan/Fact/Report union을 갖고 있습니다.
  새 Frontend(설계 4단계) 전까지는 의도된 유지입니다.
- Legacy runner(fixture/gemini journey)와 legacy Run 분기는 프로그램 경로로만 남아
  있고 공용 API에서는 쓰이지 않습니다.
- 코디네이터가 `CustomerSignalPack.take_outcome`으로 스냅샷 상태를 받는 결합이
  하나 있습니다. RunStore가 event fold만으로 터미널 상태를 만들 수 있게 되면 제거합니다.
- 실행 timeout의 공개 오류 코드가 `generic_run_failed`에서 `analysis_timeout`으로
  바뀌었습니다. 설계 문서의 오류 표와 일치하는 방향의 의도된 변화입니다.

## 설계 완료 기준 대비 현황

| 설계 기준 | 현황 |
| --- | --- |
| 1. 새 Pack이 모듈 + Registry 한 줄, Frontend diff 없음 | 달성 (SourceOverviewPack) |
| 2. 재시작과 단절 뒤 cursor replay가 live와 동일 | 달성 (SSE, Presentation 모두) |
| 3. 표현 실패와 Canonical Run 분리 | 달성 (wire 투영 실패 격리, notice fallback) |
| 4. Frontend에 Pack별 switch 없음 | 부분 달성 (새 Frontend 전까지 기존 decoder 유지) |
| 5. Fixture mode 전체 흐름 재현 | 달성 |
| 6. 두 번째 Pack이 trusted Catalog만으로 렌더링 | 부분 달성 (GenericRunProjector 경유, 새 Host는 미구현) |
| 7. Swagger와 api-endpoints.md 일치 | 달성 |
| 8. 관측 trace 공개 안전성 유지 | 달성 (기존 규칙 무변경) |

## 액션

- 다음 스프린트에 설계 3단계(AG-UI, CopilotKit Host Adapter)를 붙일 때
  `PresentationIntent`를 입력으로 사용
- 두 번째 Pack의 Run 생성 API가 필요해지는 시점에 `POST /api/runs`에 `pack_id`를
  받는 얇은 라우팅 추가 (404 `analysis_pack_not_found` 규칙은 설계 문서에 준비됨)
- Legacy runner 제거는 새 Frontend 검증 뒤로 유지

## 부록 A. 검증 명령

```bash
make test                                        # 전체 자동 검증
uv run --project backend pytest backend/tests/test_event_journal_contracts.py -q
uv run --project backend pytest backend/tests/test_pack_kernel.py -q
uv run --project backend pytest backend/tests/test_pack_harness.py -q
uv run --project backend pytest backend/tests/test_journal_wire_replay.py -q
uv run --project backend pytest backend/tests/test_presentation.py -q
uv run --project backend pytest backend/tests/test_primitive_catalog.py -q
```

## 부록 B. 주요 모듈 위치

| 경계 | 모듈 |
| --- | --- |
| Canonical Run Event, EventJournal | `backend/src/customer_signal/journal/` |
| Pack 계약, Registry, Kernel, Harness | `backend/src/customer_signal/packs/` |
| wire 투영 (SSE 호환 격리) | `backend/src/customer_signal/runtime/wire_projection.py` |
| Primitive 카탈로그 | `backend/src/customer_signal/domain/primitive_catalog.py` |
| Presentation 투영 | `backend/src/customer_signal/presentation/` |
| 기계 계약 | `contracts/primitive-catalog.json`, `contracts/generic-run-events.json` |
