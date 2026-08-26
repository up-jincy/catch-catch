# 신규 스키마 온보딩 파이프라인 설계 (2026-08-26)

## 목표

임의의 신규 테이블(CSV/Parquet)을 캐노니컬 `CustomerEvent` 소스로 등록해, 에이전트가 코드 수정
없이 매니페스트 기반으로 즉시 탐색하게 한다. 해커톤 범위 — 프로덕션 수준 승인 절차·UI는 제외한다.

## Part A — 스키마 개방

- `domain/models.py`의 `EventType` 리터럴 5개를 `SourceId`와 같은 패턴 제약 문자열
  (`^[a-z][a-z0-9_]{1,63}$`)로 교체한다. 별칭 이름은 유지해 참조처(`facts.py`, `reports.py`)에
  자동 전파한다.
- 안전망은 `manifest.validate_event`의 `supported_event_types` 멤버십 검사가 그대로 담당한다.
- 프론트 `contracts.ts`의 `EventType` 유니온은 `string`으로 완화한다.
- `sequences.py` 별칭 테이블과 레거시 `match_journey_pattern`은 손대지 않는다. 신규 소스는
  범용 predicate/콜론 문법을 사용한다.

## Part B — 온보딩 파이프라인 (`customer_signal/onboarding/`)

접근: LLM은 코드가 아니라 **선언적 매핑 스펙(JSON)** 만 생성하고, 범용 어댑터 1개가 해석한다.

- `profiler.py` — DuckDB로 CSV/Parquet을 읽어 `TableProfile`(컬럼·타입·null·distinct·상위값·샘플) 생성.
- `spec.py` — `SourceMappingSpec`: 캐노니컬 필드별 매핑 규칙(`column` | `const` | `value_map`),
  타임스탬프 컬럼+타임존, identity(고객 컬럼+네임스페이스), dimensions/measures(PII 분류 포함),
  라벨·설명. 변환 어휘 밖 요구는 스키마 검증에서 거부.
- `draft.py` — 스펙 초안 생성. 휴리스틱 초안(기본, LLM 불필요)과 Gemini 초안(구조화 출력,
  `GEMINI_API_KEY` 필요) 두 구현. 초안은 사람이 편집 가능한 JSON 파일로 출력.
- `adapter.py` — `MappedTableAdapter(SourceAdapter)`: 스펙+데이터 파일을 받아 행을
  `CustomerEvent`로 투영하고 매니페스트(supported set·data_interval은 데이터에서 계산)를 선언.
  identity edge는 고객 컬럼 값 ↔ `canonical_customer`를 exact로 연결. evidence는 이벤트에서
  파생한 마스킹 레코드를 제공하는 `MappedEvidenceProvider`로 서빙하고, 기존 repository provider와
  composite로 묶는다.
- `cli.py` — `draft`(프로파일→초안 파일), `register`(스펙 검증 → 전체 구간 dry-run으로 기존
  `validate_adapter_contract` 통과 확인 → PII 요약 출력 → 스펙+데이터를 등록 디렉토리로 복사).
- 와이어링 — `Settings.onboarded_sources_dir`(기본 `data/onboarded-sources`)를 api 기동 시
  스캔해 `MappedTableAdapter`들을 registry에 등록.

## 흐름

```
CSV/Parquet → draft(휴리스틱/Gemini) → spec.json 사람 검토 → register(검증+dry-run)
→ 등록 디렉토리 → api 기동 시 registry 등록 → 에이전트 즉시 분석
```

## 검증·에러

- 등록 전 3단계: pydantic 스펙 검증 → 컬럼 존재·value_map 커버리지 검증 → 전체 데이터
  dry-run으로 `validate_adapter_contract`(정렬·supported set·identity 해소) 통과.
- 매핑 실패 행은 조용히 스킵하지 않고 register 전체를 실패시킨다.

## 테스트

- 변환 규칙·스펙 거부 케이스 단위 테스트.
- 신규 event_type(기존 5개 밖)을 가진 샘플 CSV로 어댑터가 `validate_adapter_contract`와
  registry 등록을 통과하는 계약 테스트 — Part A 개방을 함께 증명.
- 기존 synthetic 테스트 전체 무변경 통과(회귀).
