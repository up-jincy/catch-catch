# 저장소 작업 원칙

## LangSmith 환경 초기화

- `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`,
  `LANGSMITH_ENDPOINT`는 Python 프로세스가 LangChain 또는 LangSmith를 import하기
  전에 환경에 있어야 합니다.
- 로컬 Backend는 `uv run --env-file <path> --project backend ...` 형태로
  시작해야 합니다. LangSmith 설정을 Uvicorn의 `--env-file`에만 전달하면 안 됩니다.
  LangSmith가 import 시점의 tracing 상태를 유지하기 때문입니다.
- 환경 파일을 선택했다면 기존 shell의 `LANGSMITH_*`, `LANGCHAIN_*` 값을 Backend
  실행 직전에 제거해야 합니다. 선택한 환경 파일의 프로젝트와 tracing 설정이 shell
  상속값보다 우선해야 합니다.
- `.env`를 shell에서 `source`하지 않습니다. Backend 실행 명령에만 전달하고
  Frontend 프로세스에는 Gemini 또는 LangSmith 값을 전달하지 않습니다.
- `.env`와 API Key 원문을 로그, 테스트 출력, 문서, Artifact에 기록하면 안 됩니다.

## LangSmith trace 구성

- Gemini 호출은 `customer_signal.<stage>` 형식의 `run_name`을 사용해야 합니다.
  `stage`는 `goal`, `plan`, `note`, `selection`, `report`처럼 역할을 나타내야 합니다.
- 각 호출에는 `customer-signal`, provider, stage 태그와 공개 가능한 metadata를
  기록해야 합니다. 이름 없는 `RunnableSequence`만으로 실행 단계를 구분하면 안
  됩니다.
- 전체 API Run을 계측할 때는 공개 `run_id`를 가진 부모 trace 하나를 만들고 모델
  호출과 분석 Primitive를 자식 span으로 연결해야 합니다.
- trace 입력과 출력에는 합성 데이터와 공개 계약만 기록합니다. API Key, 원본 PII,
  provider 원문, 비공개 추론은 기록하면 안 됩니다.
- Fixture 모드는 외부 provider를 호출하지 않으므로 LLM trace가 없어도 정상입니다.

## LangSmith 변경 검증

- Launcher를 바꾼 뒤 깨끗한 subprocess에서
  `langsmith.utils.tracing_is_enabled()`가 `True`인지 확인해야 합니다.
- 적재 복구 완료를 판단할 때는 환경변수 설정 여부만 보지 않습니다. 새 Backend를
  시작하고 실제 승인된 합성 입력을 한 번 실행한 뒤, 설정한 프로젝트에서 새 trace의
  시각, `run_name`, stage metadata를 확인해야 합니다.
- Uvicorn을 `--reload` 없이 실행했다면 코드나 `.env` 변경 뒤 Backend를 다시
  시작해야 합니다.
