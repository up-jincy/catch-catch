# Gemini 실호출 검증 기록 템플릿

- 문서 상태: 미실행 템플릿
- 템플릿 갱신일: 2026-08-20
- 실행 승인: 미기록
- 실행 commit: 미기록
- 실행자: 미기록

이 문서는 실제 Gemini 호출이 승인되고 완료된 뒤 관측값을 기록하는 템플릿입니다.
현재 내용은 실호출 성공이나 모델 품질을 주장하지 않습니다. Fixture 실행 결과를
Gemini 결과로 옮겨 적으면 안 됩니다.

## 실행 전 확인

- [ ] 실호출과 외부 비용에 대한 승인
- [ ] 합성 데이터만 사용하는지 확인
- [ ] `ENV_FILE`, 현재 checkout `.env`, main checkout `.env` 중 사용할 파일 확인
- [ ] Backend가 새 Uvicorn 프로세스인지 확인
- [ ] Uvicorn 명령에 `--env-file <resolved-path>`가 있는지 확인
- [ ] Frontend 프로세스에 Gemini 또는 LangSmith 값이 전달되지 않는지 확인
- [ ] API Key, Provider 원문, 내부 추론을 터미널 캡처와 문서에 남기지 않도록 확인

환경 파일은 복사하거나 shell에 `source`하지 않습니다. `scripts/dev.sh`가 선택한
경로를 새 Uvicorn 프로세스의 `--env-file` 인자로만 전달해야 합니다.

## 실행 입력

실행 전에 아래 값을 확정합니다.

| 항목 | 기록값 |
| --- | --- |
| 질문 | 미기록 |
| 시작 시각 | 미기록 |
| 종료 시각 | 미기록 |
| Source | 미기록 |
| Agent 모드 | `gemini` |
| 기본 모델 | 미기록 |
| 대체 모델 | 미기록 |

권장 smoke 질문은 아래 계약 질문 중 하나입니다.

```text
최근 부정 피드백이 많은 Topic과 관련 고객 Segment를 알려줘.
```

실제 호출은 승인된 작업에서만 수행합니다.

```bash
ENV_FILE=/absolute/path/to/approved.env make dev-gemini
```

## 실행 결과

실행하지 않은 항목은 `미기록`으로 유지합니다. 예상값이나 Fixture 값을 관측값으로
작성하면 안 됩니다.

| 항목 | 관측값 |
| --- | --- |
| Run ID | 미기록 |
| Run 상태 | 미기록 |
| Agent 모드 | 미기록 |
| 실제 모델 | 미기록 |
| 완료 시간 | 미기록 |
| Headline | 미기록 |
| 대표 Metric | 미기록 |
| 공개 Fact 수 | 미기록 |
| 공개 Analysis Note 수 | 미기록 |
| 공개 오류 | 미기록 |

## 공개 이벤트 확인

- [ ] `run_started`
- [ ] `goal_created` 또는 `clarification_required`
- [ ] `plan_created`
- [ ] 각 Step의 `step_started`, `fact_created`, `analysis_note_created`,
  `step_completed`
- [ ] `report_validating`
- [ ] `result`
- [ ] `done`
- [ ] 연속 증가하는 SSE `id`
- [ ] `Last-Event-ID` 이후 재연결

이벤트에는 chain-of-thought, prompt, message 원문, API Key, Raw PII가 없어야 합니다.
확인 결과와 예외가 있으면 아래에 공개 가능한 내용만 기록합니다.

```text
미기록
```

## Artifact와 다운로드 확인

- [ ] `GET /api/run-artifacts/{run_id}`
- [ ] `GET /api/run-artifacts/{run_id}/document`
- [ ] `GET /api/run-artifacts/{run_id}/download.json`
- [ ] `GET /api/run-artifacts/{run_id}/download.md`
- [ ] 재시작 뒤 History 복원
- [ ] JSON과 Markdown의 Run ID, 질문, Fact, Note 일치

## 최종 판정

| 판정 | 기록값 |
| --- | --- |
| 결과 | 미실행 |
| Blocker | 미기록 |
| 후속 작업 | 미기록 |

실제 호출이 끝난 뒤 근거가 있는 항목만 채웁니다. 실패도 그대로 기록하며 Fixture
성공으로 대체하지 않습니다.
