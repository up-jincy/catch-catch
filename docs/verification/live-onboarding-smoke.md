# 온보딩 소스 라이브 스모크 검증 기록

- 문서 상태: 완료
- 실행일: 2026-08-26
- Agent 모드: `gemini` (기본 모델 `gemini-3.7-flash`)
- 대상: 온보딩 파이프라인으로 등록한 신규 `payment` 소스 vs 기존 5개 synthetic 소스

## 목표

신규 테이블을 온보딩 파이프라인(draft → 검토 → register)으로 등록하면, 코드 수정 없이
실제 Gemini 분석 루프가 새 소스를 탐색하는지 확인한다. 같은 서버에서 기존 소스
시나리오와 나란히 실행해 플랜 수립 방식의 차이를 비교한다.

## 과정

### 데이터와 온보딩

결제 이력 CSV 123행(고객 60명, 2026년 7월, KST naive 타임스탬프)을 합성했다. 실패 33건을
iptv에 집중(22건)시키고, 실패 후 재시도 성공 고객과 실패 후 이탈 고객 패턴을 심었다.
스키마는 기존 5개 event_type 밖의 신규 도메인이다.

`draft --gemini`가 소스 설명 문장만 보고 초안을 생성했다: `Asia/Seoul` 타임존,
`S→success / F→failure` value_map, `email`을 `direct_identifier`+hash, `amount` 단위 KRW.
사람 검토 단계에서 topic을 상수 대신 `product` 컬럼으로 한 곳만 수정한 뒤 `register`가
전체 123행 dry-run 검증을 통과시키고 `backend/data/onboarded-sources/payment/`에 등록했다.
API 재기동만으로 `/api/sources`에 6번째 소스로 나타났다.

### 라이브 실행 비교

같은 Gemini 서버에 두 Run을 실행했고 둘 다 `completed`로 끝났다.

| | 신규 payment 소스 | 기존 5개 소스 |
| --- | --- | --- |
| 질문 | 7월에 결제 실패 뒤 재시도로 성공한 고객 수와 실패 집중 상품 | AI 검색 실패 후 고객센터까지 문의한 고객 수 |
| Goal | 재시도 성공 고객 집계 + 상품별 실패 집중도 | 검색 실패 후 상담 전환 고객 산출 |
| Plan | `catalog_sources` → `profile_events`(group_by product, `outcome == 'failure'`) → `match_sequence` | `catalog_sources` → `profile_events` → `match_sequence` |
| sequence 토큰 | 범용 콜론 문법 `payment_attempt:failure` → `payment_attempt:success` | 하드코딩 별칭 `search_failed` → `support_contact` |
| 결과 | 실패 고객 26명·실패 33건, 매칭 5명 | 고객 30명·이벤트 84건, 매칭 10명 |

플랜 구조는 동일했고, 차이는 토큰 표현뿐이었다. 신규 소스에는 별칭 테이블이 없으므로
Gemini가 매니페스트의 event_type과 outcome을 조합한 범용 콜론 문법을 스스로 사용했다 —
설계에서 예측한 대로다. 보고서·Fact·Claim·evidence(`ev-payment-*`) 체계도 신규 소스에서
기존과 동일하게 동작했다.

## 결론

- 온보딩 파이프라인 → 라이브 Gemini 분석까지 코드 수정 없이 end-to-end로 동작한다.
- Gemini 초안 품질은 설명 한 문장으로 타임존·value_map·PII 분류까지 추론하는 수준이며,
  사람 검토는 분석 축 선택(topic) 같은 판단만 남는다.
- **발견(기존 시스템 공통)**: `match_sequence`가 매칭 고객 목록을
  `min(max_output_rows, max_evidence)`로 자른 뒤 `matched_customer_count`를 세므로,
  카운트가 evidence 캡에 물린다. payment run은 실측 16명이 캡 5로, 기존 소스 run도
  캡 10에 정확히 물려 보고됐다. 온보딩과 무관한 기존 primitive 동작이며, 카운트를
  자르기 전에 세도록 고치면 해결된다.

## 액션

- `match_sequence`의 카운트-캡 순서 수정 검토 (기존 회귀 기준값 6/6/5 재검증 필요).
- 시연 시 매칭 수는 "상위 N명 기준"으로 설명하거나 수정 후 시연.

## 부록: Run ID

| 시나리오 | Run ID |
| --- | --- |
| payment 소스 | `18a7fae1-3978-4906-954c-d31c261da56d` |
| 기존 5개 소스 | `00a68a1c-e282-46d3-be7c-1a2bb4b9c509` |
