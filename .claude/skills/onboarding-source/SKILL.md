---
name: onboarding-source
description: Use when a new raw table (CSV/Parquet) needs to become an analysis-agent source in this repo — "이 데이터 넣어줘", "새 테이블/스키마 온보딩", "소스로 등록", or when /api/sources lacks a source the user expects.
---

# 신규 테이블 소스 온보딩

## Overview

CSV/Parquet을 코드 수정 없이 캐노니컬 이벤트 소스로 등록한다.
흐름: `draft`(스펙 초안) → 사람 검토 → `register`(검증+등록) → 서버 재시작.

**모든 명령은 레포 루트에서 실행한다** — 등록 디렉토리(`data/onboarded-sources`)와
서버 스캔 경로가 cwd 기준 상대 경로다.

## 명령

```bash
# 1) 초안 생성 — Gemini 초안(권장, 키는 backend/.env에서 로드)
uv run --env-file backend/.env --project backend \
  python -m customer_signal.onboarding.cli draft --gemini \
  --file <table.csv> --source-id <id> --label "<Label>" \
  --description "<도메인 설명. 코드값 의미(S=성공 등)와 타임존을 문장으로 적으면 초안 품질이 크게 오른다>" \
  --out /tmp/<id>-spec.json
# 키 없으면 --gemini 빼면 휴리스틱 초안

# 2) 사람 검토 후 등록 (--registry-dir 생략 시 data/onboarded-sources)
uv run --project backend python -m customer_signal.onboarding.cli register \
  --spec /tmp/<id>-spec.json --file <table.csv>

# 3) 반영: 기동 시 1회 스캔이므로 서버 재시작 필수
make dev   # Gemini 모드 기본
# 4) 확인
curl -s http://127.0.0.1:8000/api/sources   # 새 source_id가 보여야 함
```

## 검토 체크리스트 (2단계, 사람 판단이 필요한 것만)

| 필드 | 확인 |
| --- | --- |
| `timezone` | naive 타임스탬프에 적용. 한국 데이터면 `Asia/Seoul` (기본 UTC) |
| `event_type` | 자유 지정 가능(`^[a-z][a-z0-9_]{1,63}$`). 기존 5종에 안 맞춰도 됨 |
| `topic`/`outcome` | 분석 축이 되므로 상수보다 컬럼 매핑 우선. 코드값은 `value_map`으로 의미화 |
| `identity` | `customer_column` 정확한지. 교차 소스 여정 연결은 고객 ID 값이 기존 소스와 같아야 함 |
| PII | `pii_classification != "none"`이면 `masking` 필수(반대도 검증됨). 초안을 믿지 말고 직접 판단 |

참고 완성본: `data/onboarded-sources/payment/spec.json`

## 함정

- **10,000행 상한** — 초과 시 draft/register 모두 거부.
- **value_map 미커버 값 = 전체 실패** — 행 스킵 없음. `default` 지정 또는 전수 매핑.
- 등록 디렉토리(`data/onboarded-sources/<id>/`)엔 데이터 파일이 정확히 1개여야 서버가 뜬다. 롤백은 디렉토리 삭제.
- `ONBOARDED_SOURCES_DIR` 환경변수로 스캔 경로 변경 가능(pydantic-settings).
- register 검증은 기존 `validate_adapter_contract` dry-run 전체 통과라 느슨하지 않다 — 실패 메시지의 행 번호를 보고 스펙을 고친다.

관련 코드: `backend/src/customer_signal/onboarding/`(cli·spec·adapter),
`backend/src/customer_signal/api.py`의 `load_onboarded_adapters` 와이어링.
