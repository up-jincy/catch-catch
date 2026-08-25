# Signal Trace 팀 데모 가이드

이 문서는 Signal Trace 프로토타입을 처음 보는 팀원에게 기능, 데이터 생성 방식,
에이전트 역할을 쉽게 설명하고 실제 화면으로 시연하기 위한 안내서입니다.

## 30초 설명

Signal Trace는 여러 채널에 흩어진 고객 행동을 자연어 질문으로 분석하는
프로토타입입니다. 사용자가 질문, 기간, Source를 고르면 에이전트가 분석 목표와
실행 순서를 정합니다. 실제 고객 수와 이벤트 수는 서버의 분석 함수가 DuckDB를
조회해 계산합니다. 서버는 계산 결과와 근거의 연결을 다시 검사한 뒤 화면에
`Goal`, `Plan`, `Fact`, `Analysis Note`, 최종 문서 순서로 보여줍니다.

쉽게 비유하면 다음과 같습니다.

| 구성 요소 | 쉬운 설명 | 실제 역할 |
| --- | --- | --- |
| 에이전트 | 분석 순서를 정하는 분석가 | 질문 해석, `Goal` 작성, `Plan` 작성, 다음 단계 선택, 보고서 초안 작성 |
| Analytics Primitive | 숫자를 계산하는 계산기 | 이벤트 집계, 고객 Segment 구성, 행동 순서 매칭, Journey와 Evidence 조회 |
| Validator | 결과를 확인하는 검수자 | Source 범위, 실행 순서, Fact와 Claim 연결, 공개 가능 범위 검증 |
| DuckDB | 합성 고객 기록이 든 로컬 데이터베이스 | 고객, 이벤트, 마스킹 Evidence, Source 간 식별자 연결 저장 |
| SSE | 진행 상황 중계 | 서버의 실행 상태를 화면에 실시간 전달 |

에이전트가 숫자를 지어내지 않는다는 점이 이 프로토타입의 설명 포인트입니다.
Fixture와 Gemini 모두 고객 수와 이벤트 수는 같은 Analytics Primitive가 계산합니다.

## 무엇을 보여주는 프로토타입인가

사용자는 다음 흐름을 한 화면에서 확인할 수 있습니다.

1. 자연어 질문, 기간, 분석할 Source를 선택합니다.
2. 에이전트가 질문을 분석 목표인 `Goal`로 바꿉니다.
3. 에이전트가 실행할 함수와 순서를 `Plan`으로 만듭니다.
4. 서버가 각 단계를 실행해 검증된 `Fact`를 만듭니다.
5. 에이전트가 Fact만 인용하는 `Analysis Note`와 보고서 초안을 만듭니다.
6. 서버가 인용 관계를 검사한 뒤 최종 결과를 공개합니다.
7. 완료한 Run은 History에서 다시 열고 JSON 또는 Markdown으로 내려받을 수 있습니다.

화면에 모델의 비공개 추론이나 Provider 원문은 표시하지 않습니다. 실행 단계,
조회 Source, 스캔 건수, 매칭 건수, 반환 건수, Result ID, Evidence ID처럼 팀원이
검증할 수 있는 정보만 표시합니다.

## 데이터는 어떻게 만들어지는가

현재 데모는 실제 고객 데이터나 운영 Connector를 사용하지 않습니다. 고정 Seed
`20260819`로 만든 합성 데이터만 사용합니다.

### 현재 데이터 규모

| 항목 | 건수 |
| --- | ---: |
| 합성 고객 | 30명 |
| 고객 이벤트 | 199건 |
| 마스킹 Evidence | 199건 |
| Source 간 식별자 연결 | 150건 |

이벤트는 다음 5개 Source로 나뉩니다.

| Source ID | 의미 | 이벤트 수 |
| --- | --- | ---: |
| `search_history` | 검색 행동과 검색 결과 | 54건 |
| `search_feedback` | 검색 결과에 대한 평가 | 36건 |
| `digital_behavior` | 지원 페이지와 Funnel 행동 | 30건 |
| `subscription` | 가입과 상품 상태 | 49건 |
| `voc` | 고객센터 문의 | 30건 |

### 생성부터 적재까지

```text
고정 Seed
  -> 시나리오별 합성 고객과 이벤트 생성
  -> 이벤트마다 마스킹 Evidence 생성
  -> Source별 식별자를 하나의 합성 고객으로 연결
  -> 데이터 계약과 중복 여부 검증
  -> DuckDB 임시 파일 작성
  -> 검증 완료 후 최종 파일로 교체
```

관련 코드는 다음 위치에 있습니다.

- 시나리오와 이벤트 생성: `backend/src/customer_signal/synthetic/generator.py`
- DuckDB 테이블 생성과 원자적 적재: `backend/src/customer_signal/data/database.py`
- 적재 명령: `backend/src/customer_signal/data/cli.py`
- 생성 결과 계약: `backend/src/customer_signal/domain/models.py`

`make seed`를 실행하면 생성기가 데이터를 다시 만들고
`data/generated/customer_signal.duckdb`에 넣습니다.

```bash
make seed
```

같은 Seed를 사용하면 같은 고객과 같은 이벤트가 만들어지므로 발표 결과가 매번
같습니다. 서버 시작 시 데이터베이스가 없거나 현재 계약과 맞지 않으면 서버도 합성
데이터를 다시 생성합니다.

### 합성 이벤트를 추가하는 방법

데모 시나리오를 바꿀 때는 DuckDB를 직접 수정하지 않습니다.
`generator.py`에서 이벤트를 추가한 뒤 `make seed`를 다시 실행합니다.

이벤트 하나를 만들 때 사람이 지정하는 대표 값은 다음과 같습니다.

```python
builder.add_event(
    customer_id="CUST-013",
    occurred_at=WINDOW_START + timedelta(days=27),
    source_id="search_feedback",
    event_type="feedback",
    action="submit_feedback",
    topic="요금제 변경",
    outcome="negative",
    text="요금제 변경 안내가 불분명해 불만을 남겼습니다.",
    attributes={"rating": 1},
)
```

생성기는 `event_id`, `evidence_id`, 마스킹 고객 ID, Source별 식별자 연결을 자동으로
붙입니다. 데이터 생성이 끝나면 고객 ID 중복, 이벤트 ID 중복, Evidence 누락,
Source와 Evidence 불일치, 식별자 연결 오류를 검사합니다.

원본 생성 결과를 JSON으로 확인할 때는 다음 명령을 사용할 수 있습니다.

```bash
uv run --project backend python -m customer_signal.synthetic.cli \
  --seed 20260819 \
  --output data/generated/customer_signal.json
```

현재 UI에는 CSV 업로드나 임의 데이터 적재 기능이 없습니다. 실제 Source를 붙이려면
Source manifest와 read-only adapter를 구현하고 Registry에 등록해야 합니다. 현재
프로토타입은 운영 데이터 쓰기, CRM 수정, 원본 개인정보 내보내기를 지원하지 않습니다.

## 에이전트는 정확히 무엇을 하는가

에이전트의 역할은 분석을 계획하고 설명하는 것입니다. 데이터 조회 범위를 넓히거나
계산 결과를 직접 확정할 권한은 없습니다.

### 1. 질문을 분석 목표로 바꿉니다

질문, 시작일, 종료일, 선택한 Source를 받아 `Goal`을 만듭니다. `Goal`에는 분석할
고객 범위, 기간, Source, 지표, 결과 형태가 들어갑니다.

### 2. 실행 계획을 만듭니다

에이전트는 허용된 함수 중 필요한 함수를 골라 `Plan`을 만듭니다. 대표 함수는 다음과
같습니다.

| Primitive | 역할 |
| --- | --- |
| `catalog_sources` | 사용 가능한 Source와 데이터 범위 확인 |
| `profile_events` | 고객 수와 이벤트 수 확인 |
| `aggregate_events` | Topic이나 결과별 이벤트 집계 |
| `segment_customers` | 조건에 맞는 고객 집합 구성 |
| `match_sequence` | 정해진 순서로 행동한 고객 탐색 |
| `get_customer_journey` | 대표 고객의 시간순 Journey 조회 |
| `get_evidence` | 결과를 뒷받침하는 마스킹 Evidence 조회 |

### 3. 서버가 계획을 검증합니다

서버는 에이전트가 선택하지 않은 Source를 새로 조회하지 못하게 막습니다. 함수 지원
여부, 단계 의존성, 최대 입력과 출력, 제한 시간도 검사합니다. 잘못된 초기 Plan은 한
번만 수정 기회를 주며 다시 실패하면 Run을 실패 처리합니다.

### 4. Analytics가 숫자를 계산합니다

각 Primitive가 DuckDB에서 해당 기간과 Source의 이벤트를 읽어 `Fact`를 만듭니다.
Fact에는 지표, 단위, 고객 ID, Evidence ID, 스캔 건수, 매칭 건수, 반환 건수,
데이터셋 버전이 들어갑니다.

### 5. 에이전트가 다음 단계와 설명을 정합니다

에이전트는 현재 Fact를 보고 다음 Plan 단계를 실행할지, 계획을 제한적으로 수정할지,
분석을 끝낼지 정합니다. 각 단계의 `Analysis Note`는 이미 생성된 Fact만 인용할 수
있습니다.

### 6. 서버가 보고서를 최종 검증합니다

서버는 보고서의 Claim이 같은 Run의 Fact를 올바르게 인용하는지 검사합니다. 검증을
통과한 결과만 UI와 다운로드 문서에 들어갑니다. 실패하더라도 공개 가능한 부분 기록은
Run에 남깁니다.

## Fixture와 Gemini의 차이

팀 설명과 기능 확인에는 Fixture 모드를 권장합니다.

| 모드 | 에이전트 동작 | 사용 시점 |
| --- | --- | --- |
| Fixture | 미리 정의한 세 질문의 `Goal`과 `Plan`을 항상 같은 방식으로 생성 | 발표, 개발, 회귀 테스트 |
| Gemini | Gemini가 질문에 맞는 `Goal`과 `Plan`을 동적으로 생성 | 동적 Planner 확인 |
| Auto | Gemini를 먼저 사용하고 사용할 수 없으면 공개 전환 이벤트를 남긴 뒤 Fixture로 전환 | 통합 동작 확인 |

Fixture 모드는 API Key와 외부 네트워크가 필요 없습니다. Gemini 모드에서도 숫자와
고객 매칭은 Analytics Primitive가 계산하고 서버가 검증합니다.

Gemini, LangSmith, Langfuse 설정을 사용하는 경우 `.env`를 shell에서 `source`하지
않습니다. 반드시 저장소의 실행 명령에 `ENV_FILE`을 전달합니다. API Key와 관측
설정은 Backend에만 전달되며 로그, 문서, Artifact에 원문을 남기면 안 됩니다.

### Langfuse로 Agent 안쪽까지 보여주기

Gemini 데모를 한 번 실행하고 화면에 표시된 Run ID를 복사합니다. Langfuse에서 같은
값의 Session을 엽니다. API Run 하나가 `customer_signal.turn` Trace 하나로 보이고,
그 아래 단계를 펼치면서 팀원에게 다음처럼 설명할 수 있습니다.

1. `goal`: “이 발화를 받아 분석 목표와 범위를 정했습니다.”
2. `plan`: “사용 가능한 읽기 전용 Primitive 중 이 단계들을 골랐습니다.”
3. `tool.*`: “실제로 이 Tool에 이 Source와 조건을 넣었고, 서버가 이 Fact를
   검증했습니다.”
4. `note`와 `selection`: “현재 Fact를 요약한 뒤 다음 Tool을 고르거나 종료했습니다.”
5. `report`: “검증된 Fact만 모아 최종 문서를 만들었습니다.”

기존 Journey 질문은 `customer_signal.agent`에서 DeepAgent의 공개 Todo와 MCP Tool
호출 흐름을 함께 확인합니다. 내부 chain-of-thought나 Provider 원문 대신 공개 계획,
Tool 이름, 마스킹된 입력, 검증 출력만 보여준다고 설명하면 됩니다. Langfuse가 꺼져
있거나 적재에 실패해도 실제 분석 결과는 계속 반환됩니다.

## 실제 서버 실행

### 최초 한 번

```bash
make setup
```

Python, Node 의존성과 Playwright Chromium을 설치합니다.

### 발표할 때

```bash
make seed
make dev-fixture
```

브라우저에서 `http://127.0.0.1:3000`을 엽니다. Backend 상태는 다음 명령으로
확인합니다.

```bash
curl http://127.0.0.1:8000/health
```

정상이면 `{"status":"ok"}`가 반환됩니다. `make dev-fixture`를 실행한 터미널에서
`Ctrl-C`를 누르면 이 명령이 시작한 Backend와 Frontend가 함께 종료됩니다.

포트 `3000` 또는 `8000`이 이미 사용 중이면 기존 프로세스를 종료하지 않고 다음처럼
다른 포트를 지정합니다.

```bash
make BACKEND_PORT=38000 FRONTEND_PORT=33000 dev-fixture
```

이 경우 화면은 `http://127.0.0.1:33000`, Backend 상태는
`http://127.0.0.1:38000/health`에서 확인합니다. 포트는 `make` 명령의 인자로
전달해야 합니다.

## 권장 데모 시나리오

대표 시연은 다음 질문을 사용합니다.

> 반복 행동 뒤 상담으로 전환되는 Journey를 보여줘.

2026년 8월 25일에 Fixture 서버에서 다시 검증한 결과는 다음과 같습니다.

| 단계 | 화면에서 확인할 결과 |
| --- | --- |
| `catalog_sources` | Source 5개, 전체 이벤트 199건 스캔 |
| `match_sequence` | 반복 행동 뒤 상담으로 이어진 고객 6명 |
| `get_customer_journey` | 대표 고객 Journey 이벤트 3건 |
| `get_evidence` | 마스킹 Evidence 3건 |

### 5분 발표 순서

1. 화면 상단을 보여주며 “여러 채널의 고객 신호를 질문 하나로 연결하는 로컬
   프로토타입입니다”라고 설명합니다.
2. 왼쪽의 추천 질문에서 “반복 행동 뒤 상담 Journey”를 선택합니다.
3. 시작일 `2026-07-20`, 종료일 `2026-08-19`를 가리킵니다. 종료일은 미포함이므로
   실제 분석 범위는 2026년 8월 18일 하루 전체까지라고 설명합니다.
4. 5개 Source가 선택된 상태에서 “분석 시작”을 누릅니다.
5. `Goal`이 나타나면 “에이전트가 질문을 분석 가능한 계약으로 바꿨습니다”라고
   설명합니다.
6. `Plan`이 나타나면 “에이전트가 Source 확인, 행동 순서 매칭, Journey 조회,
   Evidence 조회 순서로 계획했습니다”라고 설명합니다.
7. `match_sequence`의 `Matched Customer Count: 6 customers`를 가리킵니다.
   “이 숫자는 모델이 쓴 답이 아니라 DuckDB를 조회한 함수의 결과입니다”라고
   설명합니다.
8. 스캔, 매칭, 반환 건수와 Result ID를 보여주며 각 결과를 다시 확인할 수 있다고
   설명합니다.
9. Evidence ID를 열어 고객 ID가 마스킹된 근거만 공개된다는 점을 보여줍니다.
10. `Verified Notes`와 `Run Document`를 보여준 뒤 History 복원과 JSON, Markdown
    다운로드로 마무리합니다.

### 발표자가 그대로 읽을 수 있는 설명

> 사용자는 고객 데이터를 직접 조회하는 대신 자연어로 질문합니다. 에이전트는
> 질문을 목표와 실행 계획으로 바꾸지만 고객 수를 직접 만들지는 않습니다. 서버의
> 분석 함수가 고정된 합성 데이터를 읽어 숫자와 근거를 계산합니다. 서버는 에이전트의
> 설명이 그 계산 결과를 제대로 인용하는지 다시 검사합니다. 그래서 화면에서 답뿐
> 아니라 어떤 Source를 몇 건 읽었고 어떤 근거를 사용했는지 함께 볼 수 있습니다.

## 다른 추천 질문과 기대 결과

| 질문 | 확인할 결과 | 발표 적합도 |
| --- | --- | --- |
| `최근 부정 피드백이 많은 Topic과 관련 고객 Segment를 알려줘.` | `요금제 변경` 관련 부정 피드백 고객 6명 | 권장 |
| `반복 행동 뒤 상담으로 전환되는 Journey를 보여줘.` | Sequence 일치 고객 6명, 대표 Journey 3건, Evidence 3건 | 가장 권장 |
| `가입 시작 뒤 완료하지 못한 고객과 이탈 단계를 알려줘.` | 가입 시작 12명, 완료 7명, 미완료 5명 | 현재 발표 비권장 |

가입 질문은 `match_sequence` 단계에서 미완료 고객 5명을 올바르게 계산하지만, 뒤의
`segment_customers` 단계는 현재 `0명`으로 표시됩니다. 생성 이벤트의 `outcome` 값과
Segment 조건이 맞지 않기 때문입니다. 이 조건을 수정하기 전에는 대표 발표에서 이
질문을 사용하지 않는 편이 안전합니다.

## 팀원이 자주 물을 질문

### “이게 그냥 LLM 답변 화면인가요?”

아닙니다. LLM 또는 Fixture Planner는 목표와 실행 순서를 정합니다. 수치와 고객
매칭은 DuckDB를 읽는 결정론적 코드가 계산하고 서버가 근거 연결을 검증합니다.

### “같은 질문을 하면 왜 같은 결과가 나오나요?”

Fixture 모드는 같은 Seed의 같은 합성 데이터와 고정된 Plan을 사용합니다. 회귀 테스트와
발표 재현성을 위한 동작입니다.

### “실제 고객 데이터를 넣을 수 있나요?”

현재 UI에서는 넣을 수 없습니다. 이 버전은 합성 데이터 전용입니다. 실제 데이터를
연결하려면 Source별 read-only adapter, manifest, 식별자 연결 정책을 구현해야 합니다.

### “에이전트가 허용되지 않은 데이터를 조회할 수 있나요?”

서버가 사용자가 선택한 Source와 기간을 `Goal`과 각 Plan 단계에서 다시 검사합니다.
Evidence API도 현재 Run의 Fact가 허용한 ID만 반환합니다.

### “Fixture에도 에이전트가 있나요?”

있습니다. 다만 자유롭게 계획하는 모델 대신 세 가지 데모 질문에 대해 고정된 typed
`Goal`, `Plan`, `Note`, 보고서 초안을 만드는 결정론적 Planner를 사용합니다. 실행과
검증 흐름은 Gemini 모드와 같습니다.

### “실패하면 기록이 사라지나요?”

아닙니다. 서버는 공개 가능한 부분 Fact와 Note, 오류 상태를 Artifact에 남깁니다.
완료 Run과 실패 Run은 History에서 다시 확인할 수 있습니다.

## 발표 전 체크리스트

- `make seed` 실행 완료
- Fixture 모드 사용
- Backend `/health` 응답 확인
- 브라우저에서 Source 5개 표시 확인
- 권장 질문으로 Run 완료 확인
- `Matched Customer Count: 6 customers` 확인
- Evidence Drawer 열림 확인
- JSON과 Markdown 다운로드 버튼 확인
- 화면 공유 전에 다른 Run History와 터미널의 민감 정보 확인
- Gemini 데모 시 `.env`와 API Key 비공개 확인

이 프로토타입은 합성 데이터 기반의 로컬 워킹 데모입니다. 실제 고객 데이터,
운영 Connector, CRM 쓰기 기능을 포함하지 않으며 운영 용도로 사용할 수 없습니다.
