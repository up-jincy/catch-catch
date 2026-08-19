"use client";

import { useRef } from "react";

import type { RunPhase, SourceId } from "./contracts";
import { RECOMMENDED_QUESTION } from "./use-run-controller";

interface QueryComposerProps {
  question: string;
  startDate: string;
  endDate: string;
  enabledSources: SourceId[];
  isCreating: boolean;
  runPhase: RunPhase;
  submissionError: string | null;
  onQuestionChange: (value: string) => void;
  onStartDateChange: (value: string) => void;
  onEndDateChange: (value: string) => void;
  onToggleSource: (source: SourceId) => void;
  onSubmit: () => void;
}

const sources: Array<{
  id: SourceId;
  label: string;
  note: string;
  required?: boolean;
}> = [
  {
    id: "search_history",
    label: "검색 이력",
    note: "Journey 분석 필수 Source",
    required: true,
  },
  {
    id: "search_feedback",
    label: "검색 피드백",
    note: "부정 피드백 신호",
  },
  {
    id: "digital_behavior",
    label: "디지털 행동",
    note: "GA 기반 페이지·Funnel 행동",
  },
  {
    id: "subscription",
    label: "가입 정보",
    note: "상품 가입·변경 상태",
  },
  {
    id: "voc",
    label: "VOC",
    note: "고객센터 미해결 문의",
  },
];

export function QueryComposer({
  question,
  startDate,
  endDate,
  enabledSources,
  isCreating,
  runPhase,
  submissionError,
  onQuestionChange,
  onStartDateChange,
  onEndDateChange,
  onToggleSource,
  onSubmit,
}: QueryComposerProps) {
  const questionRef = useRef<HTMLTextAreaElement>(null);
  const activeRun = runPhase === "running" || runPhase === "validating";

  return (
    <section className="panel composer-panel" aria-labelledby="query-title">
      <div className="panel-heading">
        <div>
          <p className="section-kicker">01 · ASK</p>
          <h2 id="query-title">고객 신호에 질문하기</h2>
        </div>
        <span className="scope-chip">합성 고객 30명</span>
      </div>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit();
        }}
      >
        <button
          className="suggestion-card"
          type="button"
          onClick={() => {
            onQuestionChange(RECOMMENDED_QUESTION);
            questionRef.current?.focus();
          }}
        >
          <span className="suggestion-icon" aria-hidden="true">
            ↗
          </span>
          <span>
            <strong>검색 실패 후 상담 전환 고객 찾기</strong>
            <small>{RECOMMENDED_QUESTION}</small>
          </span>
        </button>

        <label className="field-label" htmlFor="analysis-question">
          분석 질문
        </label>
        <div className="question-field">
          <textarea
            ref={questionRef}
            id="analysis-question"
            rows={4}
            value={question}
            onChange={(event) => onQuestionChange(event.target.value)}
            placeholder="고객 행동과 문의 흐름을 자연어로 물어보세요"
            aria-describedby="question-hint"
          />
          <span className="question-mark" aria-hidden="true">
            AI
          </span>
        </div>
        <p id="question-hint" className="field-hint">
          현재 데모는 검색 실패 → 동일 Topic 재검색 → 고객센터 문의 흐름을 지원합니다.
        </p>

        <div className="date-grid">
          <div>
            <label className="field-label" htmlFor="start-date">
              시작일
            </label>
            <input
              id="start-date"
              type="date"
              value={startDate}
              onChange={(event) => onStartDateChange(event.target.value)}
              required
            />
          </div>
          <div>
            <label className="field-label" htmlFor="end-date">
              종료일 · 미포함
            </label>
            <input
              id="end-date"
              type="date"
              value={endDate}
              onChange={(event) => onEndDateChange(event.target.value)}
              required
            />
          </div>
        </div>
        <p className="field-hint date-hint">
          종료일은 포함하지 않아요. 기본 범위는 2026년 8월 18일까지입니다.
        </p>

        <fieldset className="source-fieldset">
          <legend>분석 Source</legend>
          <div className="source-options">
            {sources.map((source) => {
              const noteId = `source-note-${source.id}`;
              return (
                <label
                  className={`source-option ${source.required ? "is-required" : ""}`}
                  key={source.id}
                >
                  <input
                    type="checkbox"
                    checked={enabledSources.includes(source.id)}
                    disabled={source.required}
                    onChange={() => onToggleSource(source.id)}
                    aria-describedby={noteId}
                  />
                  <span className="source-check" aria-hidden="true" />
                  <span>
                    <strong>{source.label}</strong>
                    <small id={noteId}>{source.note}</small>
                  </span>
                </label>
              );
            })}
          </div>
        </fieldset>

        {submissionError ? (
          <p className="form-error" role="alert">
            {submissionError}
          </p>
        ) : null}

        <button className="primary-action" type="submit" disabled={isCreating}>
          <span aria-hidden="true">{isCreating ? "···" : "▶"}</span>
          {isCreating
            ? "Run 생성 중"
            : activeRun
              ? "새 분석 시작"
              : "분석 시작"}
        </button>
      </form>
    </section>
  );
}
