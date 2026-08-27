# 해커톤 기민성 런북

해커톤 중 자주 하는 변경의 실행 절차입니다. 배경과 경계 설명은
[구조 정리 리포트](superpowers/reports/2026-08-28-hackathon-agility-restructure.md)를 참고합니다.

## 레시피 1. 새 데이터 Source 붙이기

CSV 또는 Parquet 테이블 하나를 분석 가능한 Source로 등록하는 절차입니다.

```bash
# 1) 테이블 프로파일링과 spec 초안 생성 (Gemini 사용 시 --gemini 추가)
uv run --project backend python -m customer_signal.onboarding.cli draft \
  --file path/to/table.csv \
  --source-id my_source --label "내 소스" --description "설명" \
  --out /tmp/my_source.spec.json

# 2) 초안 검토 후 등록 (검증 실패 시 등록되지 않음)
uv run --project backend python -m customer_signal.onboarding.cli register \
  --spec /tmp/my_source.spec.json --file path/to/table.csv \
  --registry-dir data/onboarded-sources

# 3) Backend 재시작 후 확인
curl -s http://127.0.0.1:8000/api/sources | jq '.items[].source_id'
```

- Backend는 시작 시 `data/onboarded-sources`를 스캔해 자동 등록합니다.
- masking, identity, evidence 규칙은 SourceRegistry가 소유하므로 spec만 작성하면 됩니다.

## 레시피 2. 새 분석 Pack 붙이기

분석 방식 하나를 추가하는 절차입니다. 참고 구현은
[source_overview.py](../backend/src/customer_signal/packs/source_overview.py)입니다.

1. `backend/src/customer_signal/packs/<my_pack>.py` 작성
   - `Input` Pydantic 모델과 `spec: AnalysisPackSpec` 선언 (goal/plan/fact/report schema 포함)
   - `execute()`에서 `GoalDraft → PlanDraft → FactDraft → ReportDraft → OutcomeDraft` 순서로 yield
   - lifecycle, sequence, 저장은 Kernel 소관이므로 Pack에서 만들지 않음
2. composition root 등록 — `api.py`의 `AnalysisPackRegistry([...])`에 한 줄 추가
3. 하니스 검증 테스트 추가

```python
from customer_signal.packs.harness import assert_pack_contract

async def test_my_pack_passes_the_harness(dependencies):
    report = await assert_pack_contract(MyPack(...), {"question": "...", ...})
    assert report.status == "completed"
```

- 하니스가 방출 순서, 터미널 1개, 공개 안전성, 결정론, 프로젝터 순수성을 한 번에 검사합니다.
- 안전한 도메인 오류는 `PackDomainError(code, message)`, 데이터 없음 종료는
  `PackDegraded((사유,))`로 던지면 Kernel이 공개 terminal event로 정규화합니다.
- 현재 `POST /api/runs`는 customer_signal 전용이므로, 새 Pack의 Run은
  `PackKernel.run()` 직접 호출 또는 테스트로 실행합니다.

## 레시피 3. 새 Primitive 붙이기

1. `domain/primitives.py`에 입력 모델, `domain/facts.py`에 payload 모델 추가 후
   `domain/types.py`의 `GenericPrimitiveName` Literal에 이름 추가
2. `domain/primitive_catalog.py`의 `PRIMITIVE_DEFINITIONS`에 정의 1건 추가
   (arity, 필수 metric key, 한국어 설명, objective)
3. `analytics/primitives/`에 핸들러 구현과 `HANDLERS` 등록
4. 기계 계약 재생성과 프론트 라벨 추가

```bash
uv run --project backend python -c \
  "from customer_signal.domain.primitive_catalog import render_contract_json; \
   open('contracts/primitive-catalog.json','w').write(render_contract_json())"
# frontend/src/features/customer-intelligence/primitive-catalog.ts 의 primitiveLabels에 라벨 추가
uv run --project backend pytest backend/tests/test_primitive_catalog.py -q
npm --prefix frontend test -- --run primitive-catalog-sync
```

- 정의를 빠뜨리면 import 시점 fail-fast, JSON을 재생성하지 않으면 drift 테스트가 실패합니다.

## 레시피 4. 표현 구조 바꾸기

- 전 Pack 공통 표현 변경: `presentation/generic.py`의 `GenericRunProjector` 수정
- 특정 Pack 전용 표현: Pack 클래스에 `projector` 속성으로 `PackProjector` 구현 부여
- 확인: `GET /api/runs/{run_id}/presentation`이 저널에서 재계산한 Intent를 반환

규칙은 두 가지입니다. 프로젝터는 순수 함수여야 하고(clock, random, I/O 금지),
catalog_key는 `presentation/intents.py`의 trusted key만 사용합니다.
분석 코드와 저장 코드는 표현 변경에서 건드리지 않습니다.

## 레시피 5. SSE 어휘 또는 화면 계약 바꾸기

기존 Frontend가 쓰는 SSE 계약은 `runtime/wire_projection.py` 한 파일에 격리되어
있습니다. canonical event를 다른 wire 어휘로 바꾸고 싶으면 이 파일만 수정하고,
`contracts/generic-run-events.json`과 `backend/tests/test_journal_wire_replay.py`를
함께 갱신합니다. Pack, Kernel, 저널은 wire 변경의 영향을 받지 않습니다.

## 레시피 6. 실행 모드 전환과 저널 운영

```bash
make dev            # Gemini 모드 (기본)
make dev-fixture    # 결정론적 fixture 모드
make dev-auto       # API Key 유무로 자동 선택
```

- Run별 요청 단위 전환: `POST /api/runs?mode=fixture|gemini`
- 저널 파일: `data/run-artifacts/event-journal.sqlite3`
  (`JOURNAL_PATH` 환경변수로 재지정 가능)
- Backend 재시작 시 저널을 replay해 완료 Run의 SSE 재전송과 Presentation 재계산을
  복원합니다. 저널 파일을 지우면 과거 replay만 사라지고 스냅샷 조회는 유지됩니다.

## 검증 명령 모음

```bash
make test                                                  # 전체 (backend + frontend + build)
uv run --project backend pytest backend/tests -q           # backend 전체
uv run --project backend pytest backend/tests/test_pack_harness.py -q   # Pack 계약
npm --prefix frontend test -- --run                        # frontend 전체
make e2e                                                   # fixture 기반 브라우저 E2E
```

## 트러블슈팅

| 증상 | 원인과 조치 |
| --- | --- |
| `analysis_pack_contract_violation` | Pack 방출 순서 위반 또는 schema 불일치. 하니스 테스트를 먼저 실행해 어떤 방출에서 깨지는지 확인 |
| `analysis_timeout` | Kernel timeout(130초) 초과. Pack 내부 timeout을 그보다 짧게 유지 |
| `unknown_source` 또는 422 | 요청 `enabled_sources`가 Registry에 없음. `/api/sources`로 등록 상태 확인 |
| Registry가 시작 시 예외 | fail-fast 정상 동작. 메시지의 pack_id와 schema_id 충돌을 해소 |
| 재시작 뒤 옛 Run replay가 비어 있음 | 저널 파일 삭제 또는 경로 변경이 원인. 스냅샷 조회는 Artifact로 계속 가능 |
| `primitive-catalog` 테스트 실패 | 카탈로그 수정 후 JSON 미재생성 또는 프론트 라벨 누락. 레시피 3의 4번 재실행 |
