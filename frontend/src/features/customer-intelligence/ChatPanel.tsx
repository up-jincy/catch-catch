"use client";

import {
  type ComponentProps,
  type ReactNode,
  useEffect,
  useRef,
  useState,
} from "react";

import { AgentTrace } from "./AgentTrace";
import { QueryComposer } from "./QueryComposer";
import type { RunState } from "./run-reducer";

interface ChatPanelProps {
  composerProps: ComponentProps<typeof QueryComposer>;
  state: RunState;
  isCreating: boolean;
  clarificationError: string | null;
  onSubmitClarification: (answer: string) => void;
  history: ReactNode;
}

export function ChatPanel({
  composerProps,
  state,
  isCreating,
  clarificationError,
  onSubmitClarification,
  history,
}: ChatPanelProps) {
  const [answer, setAnswer] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  const answerRef = useRef<HTMLTextAreaElement>(null);
  const clarificationId = state.clarification?.clarification_id ?? null;

  useEffect(() => {
    if (!clarificationId || state.clarification?.answer) return;
    setAnswer("");
    setLocalError(null);
    answerRef.current?.focus();
  }, [clarificationId, state.clarification?.answer]);

  const suggestions = state.suggestedQuestions;

  return (
    <aside className="control-rail chat-rail" aria-label="질문과 Run 기록">
      <QueryComposer {...composerProps} />

      <section className="panel chat-panel" aria-labelledby="chat-title">
        <div className="panel-heading">
          <div>
            <p className="section-kicker">02 · CHAT</p>
            <h2 id="chat-title">분석 대화</h2>
          </div>
          {state.runId ? <span className="run-id-chip">{state.runId.slice(0, 8)}</span> : null}
        </div>

        <div
          className="chat-log"
          role="log"
          aria-label="분석 대화"
          aria-live="polite"
          aria-relevant="additions text"
        >
          {state.request ? (
            <article className="chat-bubble chat-bubble-user">
              <span>나</span>
              <p>{state.request.question}</p>
            </article>
          ) : (
            <p className="chat-empty">질문을 실행하면 공개 가능한 진행 상태를 여기에 기록합니다.</p>
          )}

          {state.goal ? (
            <article className="chat-bubble chat-bubble-agent">
              <span>분석 목표</span>
              <p>{state.goal.objective}</p>
            </article>
          ) : null}

          {state.clarification ? (
            <article className="chat-bubble chat-bubble-agent clarification-bubble">
              <span>확인 질문</span>
              <p>{state.clarification.question}</p>
              {state.clarification.answer ? (
                <p className="clarification-answer">답변 · {state.clarification.answer}</p>
              ) : (
                <form
                  className="clarification-form"
                  onSubmit={(event) => {
                    event.preventDefault();
                    const normalized = answer.trim();
                    if (!normalized) {
                      setLocalError("확인 답변을 입력해 주세요.");
                      return;
                    }
                    setLocalError(null);
                    onSubmitClarification(normalized);
                  }}
                >
                  <label htmlFor="clarification-answer">확인 답변</label>
                  <textarea
                    ref={answerRef}
                    id="clarification-answer"
                    rows={3}
                    value={answer}
                    onChange={(event) => setAnswer(event.target.value)}
                  />
                  {localError || clarificationError ? (
                    <p className="form-error" role="alert">
                      {localError ?? clarificationError}
                    </p>
                  ) : null}
                  <button className="primary-action" type="submit">
                    답변하고 계속
                  </button>
                </form>
              )}
            </article>
          ) : null}

          {state.error &&
          (state.error.code === "unsupported_analysis" || suggestions.length) ? (
            <article className="chat-bubble chat-bubble-error">
              <span>Run 안내</span>
              <p>{state.error.message}</p>
              {suggestions.length ? (
                <div className="safe-suggestions" aria-label="추천 질문">
                  {suggestions.map((suggestion) => (
                    <button
                      type="button"
                      key={suggestion}
                      onClick={() => composerProps.onQuestionChange(suggestion)}
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              ) : null}
            </article>
          ) : null}
        </div>
      </section>

      <AgentTrace
        events={state.events}
        phase={state.phase}
        fallbackReason={state.fallbackReason}
        isCreating={isCreating}
      />
      {history}
    </aside>
  );
}
