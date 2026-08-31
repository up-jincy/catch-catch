"use client";

import { useEffect, useRef, useState } from "react";

import type { SourceId } from "../../customer-intelligence/contracts";
import { PLACEHOLDER_QUESTIONS, SOURCE_OPTIONS, SUGGESTIONS } from "../state/mock";

import { ComposerMenu, PERIOD_CHOICES, REQUIRED_SOURCE, type PeriodChoice } from "./ComposerMenu";
import { TypewriterPlaceholder } from "./TypewriterPlaceholder";
import styles from "./ask.module.css";

interface AskScreenProps {
  question: string;
  onQuestionChange: (value: string) => void;
  onSubmit: () => void;
  /** unsupported_analysis 또는 실패로 되돌아왔을 때의 안내 문구. */
  notice: string | null;
  /** 되돌아왔을 때 백엔드가 준 대안 질문. 있으면 제안 카드를 대체한다. */
  suggestedQuestions: string[];
}

export function AskScreen({
  question,
  onQuestionChange,
  onSubmit,
  notice,
  suggestedQuestions,
}: AskScreenProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [sources, setSources] = useState<SourceId[]>(() => SOURCE_OPTIONS.map((item) => item.id));
  const [period, setPeriod] = useState<PeriodChoice>(PERIOD_CHOICES[1]);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const node = inputRef.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${node.scrollHeight}px`;
  }, [question]);

  function toggleSource(id: SourceId) {
    if (id === REQUIRED_SOURCE) return;
    setSources((prev) => (prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]));
  }

  function submit() {
    if (!question.trim()) {
      inputRef.current?.focus();
      return;
    }
    onSubmit();
  }

  const cards = suggestedQuestions.length
    ? suggestedQuestions.map((text) => ({ persona: "추천", label: text, question: text }))
    : SUGGESTIONS.map((item) => ({ ...item }));

  return (
    <div className={styles.screen}>
      <div className={styles.center}>
        <p className={styles.kicker}>고객은 말보다 먼저 신호를 보냅니다</p>
        <h1 className={styles.title}>고객의 시그널을 찾아보세요</h1>

        {notice ? (
          <div className={styles.notice} role="status">
            <p>{notice}</p>
          </div>
        ) : null}

        <div className={styles.composerWrap}>
          <div className={styles.tokens}>
            <span className={styles.token}>
              데이터셋 {sources.length}
            </span>
            <span className={styles.token}>{period.label}</span>
          </div>

          <form
            className={styles.composer}
            onSubmit={(event) => {
              event.preventDefault();
              submit();
            }}
          >
            <button
              type="button"
              className={styles.plus}
              aria-label="분석 조건 추가"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((prev) => !prev)}
            >
              +
            </button>

            <div className={styles.field}>
              <label className={styles.srOnly} htmlFor="catch-question">
                분석 질문
              </label>
              <textarea
                id="catch-question"
                ref={inputRef}
                rows={1}
                value={question}
                onChange={(event) => onQuestionChange(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    submit();
                  }
                }}
              />
              <TypewriterPlaceholder phrases={PLACEHOLDER_QUESTIONS} paused={question.length > 0} />
            </div>

            <button type="submit" className={styles.go} aria-label="시그널 캐치하기">
              <svg width="18" height="18" viewBox="0 0 20 20" aria-hidden="true">
                <circle cx="8.6" cy="8.6" r="6" fill="none" stroke="currentColor" strokeWidth="2" />
                <path
                  d="M13.2 13.2 17.4 17.4"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.4"
                  strokeLinecap="round"
                />
              </svg>
            </button>

            {menuOpen ? (
              <ComposerMenu
                sources={SOURCE_OPTIONS}
                selected={sources}
                period={period}
                onToggleSource={toggleSource}
                onSelectPeriod={setPeriod}
                onClose={() => setMenuOpen(false)}
              />
            ) : null}
          </form>
        </div>

        <ul className={styles.cards} aria-label="이런 질문은 어때요">
          {cards.map((card) => (
            <li key={card.question}>
              <button
                type="button"
                className={styles.card}
                onClick={() => {
                  onQuestionChange(card.question);
                  inputRef.current?.focus();
                }}
              >
                <span className={styles.cardTag}>{card.persona}</span>
                <span className={styles.cardLabel}>{card.label}</span>
                {card.question === card.label ? null : (
                  <span className={styles.cardQuestion}>{card.question}</span>
                )}
                <span className={styles.cardArrow} aria-hidden="true">
                  ↗
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
