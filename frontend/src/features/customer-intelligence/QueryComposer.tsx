"use client";

import { useRef } from "react";

import type { RunPhase, SourceId } from "./contracts";
import {
  KNOWN_SOURCE_OPTIONS,
  type SourceOption,
} from "./source-catalog";

export const RECOMMENDED_QUESTIONS = [
  {
    label: "부정 피드백 Topic과 Segment",
    question: "최근 부정 피드백이 많은 Topic과 관련 고객 Segment를 알려줘.",
  },
  {
    label: "반복 행동 뒤 상담 Journey",
    question: "반복 행동 뒤 상담으로 전환되는 Journey를 보여줘.",
  },
  {
    label: "가입 미완료와 이탈 단계",
    question: "가입 시작 뒤 완료하지 못한 고객과 이탈 단계를 알려줘.",
  },
] as const;

export interface QueryComposerProps {
  question: string;
  startDate: string;
  endDate: string;
  enabledSources: SourceId[];
  sourceOptions?: readonly SourceOption[];
  isCreating: boolean;
  runPhase: RunPhase;
  submissionError: string | null;
  onQuestionChange: (value: string) => void;
  onStartDateChange: (value: string) => void;
  onEndDateChange: (value: string) => void;
  onToggleSource: (source: SourceId) => void;
  onSubmit: () => void;
}

export function QueryComposer({
  question,
  startDate,
  endDate,
  enabledSources,
  sourceOptions = KNOWN_SOURCE_OPTIONS,
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
        <div className="suggestion-list" aria-label="추천 분석 질문">
          {RECOMMENDED_QUESTIONS.map((suggestion) => (
            <button
              className="suggestion-card"
              type="button"
              key={suggestion.question}
              onClick={() => {
                onQuestionChange(suggestion.question);
                questionRef.current?.focus();
              }}
            >
              <span className="suggestion-icon" aria-hidden="true">↗</span>
              <span>
                <strong>{suggestion.label}</strong>
                <small>{suggestion.question}</small>
              </span>
            </button>
          ))}
        </div>

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
          Source가 공개한 범위 안에서 집계, Segment, Journey와 이탈 단계를 분석합니다.
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
            {sourceOptions.map((source) => {
              const noteId = `source-note-${source.id}`;
              const required = source.id === "search_history";
              return (
                <label
                  className={`source-option ${required ? "is-required" : ""}`}
                  key={source.id}
                >
                  <input
                    type="checkbox"
                    checked={enabledSources.includes(source.id)}
                    disabled={required}
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
