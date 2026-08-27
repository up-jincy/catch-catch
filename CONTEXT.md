# Customer Signal 도메인 맥락

이 문서는 Customer Signal 분석 흐름에서 사용하는 핵심 용어를 정의합니다. 코드, 공개 계약,
설계 문서는 같은 뜻에 같은 용어를 사용해야 합니다.

## 핵심 용어

### Analysis Run

사용자 요청 하나를 입력으로 받아 분석 결과를 만들고 종료하는 실행 단위입니다. Run은 생성할 때
선택한 Analysis Pack 버전과 공개 입력을 고정합니다. Run의 상태는 Canonical Run Event를 순서대로
적용해 계산합니다.

### Analysis Pack

한 종류의 분석에 필요한 입력 계약, Goal, Plan, Fact, Report 규칙, 실행 로직과 공개 표현 의도를
모은 Backend Module입니다. 새 분석은 `AnalysisPackAdapter`를 구현하고 명시적 Registry에 등록합니다.

### Canonical Run Event

Analysis Run에서 확인된 상태 변화를 나타내는 append-only 공개 기록입니다. Event는 Run별 연속
sequence를 가지며 EventJournal에 commit된 뒤에만 외부 stream으로 전달합니다. Frontend 화면과
완료 snapshot은 이 기록에서 다시 만들 수 있습니다.

### Analysis Artifact

Goal, Plan, Fact, Report처럼 Analysis Pack이 만든 버전 있는 공개 값입니다. Artifact는 `schema_id`와
검증된 JSON 값을 가지며 Canonical Run Event의 `artifact.committed` payload로 기록합니다.

### Presentation Intent

Canonical Run Event를 사용자에게 어떤 화면으로 보여줄지 나타내는 protocol-neutral 공개 표현입니다.
Presentation Intent는 Analysis Run의 정본이 아니며 같은 Event에서 다시 계산할 수 있습니다.

### Trusted Catalog

Frontend가 실행할 수 있다고 승인한 domain-neutral React renderer와 prop schema의 목록입니다.
Analysis Pack은 Catalog key와 공개 data binding만 지정하며 React Implementation을 직접 참조하지
않습니다.

### Host Adapter

Frontend Shell을 외부 Agent UI runtime에 연결하는 Adapter입니다. 첫 production Adapter는
CopilotKit v2를 사용하고, 테스트 Adapter는 같은 Interface에서 메모리 stream을 제공합니다.

### Projection Adapter

Presentation Intent를 A2UI 같은 구체적인 표현 protocol message로 변환하고 schema를 검증하는
Adapter입니다. Projection 실패는 Canonical Run의 성공 또는 실패 상태를 바꾸지 않습니다.

### EventJournal

Canonical Run Event를 원자적으로 저장하고 cursor 기반 read와 tail을 제공하는 Backend Module입니다.
로컬 기본 Adapter는 SQLite, 테스트 Adapter는 메모리 Implementation을 사용합니다.
