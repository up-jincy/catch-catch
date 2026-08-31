"use client";

import { useEffect, useRef, useState } from "react";

import { Overlay } from "../Overlay";
import type { ClarificationPrompt } from "../state/types";

import styles from "./catching.module.css";

const TYPE_MS = 32;

interface ClarificationModalProps {
  prompt: ClarificationPrompt;
  onAnswer: (answer: string) => void;
}

/**
 * 대화형 UI 대신 쓰는 단 한 번의 인터럽트.
 * 진행이 멈춘 순간을 약점이 아니라 '단서를 좁히는 장면'으로 연출한다.
 */
export function ClarificationModal({ prompt, onAnswer }: ClarificationModalProps) {
  const [typed, setTyped] = useState("");
  const [answer, setAnswer] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (typed.length >= prompt.question.length) {
      inputRef.current?.focus();
      return;
    }
    const timer = setTimeout(
      () => setTyped(prompt.question.slice(0, typed.length + 1)),
      TYPE_MS,
    );
    return () => clearTimeout(timer);
  }, [typed, prompt.question]);

  // 백엔드가 답을 기다리는 중이라 닫기 없이 focus 만 가둔다.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key !== "Tab") return;
      const focusable = dialogRef.current?.querySelectorAll<HTMLElement>("input, button");
      if (!focusable?.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const done = typed.length >= prompt.question.length;

  return (
    <Overlay>
      <div className={styles.scrim}>
      <div
        className={styles.modal}
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label="추가 확인"
      >
        <p className={styles.modalTag}>단서를 좁히는 중</p>
        <h2 className={styles.modalTitle}>잠깐, 하나만 더 확인할게요</h2>

        <p className={styles.modalQuestion}>
          {typed}
          {done ? null : <i className={styles.block} />}
        </p>

        {done ? <p className={styles.modalHint}>{prompt.hint}</p> : null}

        <form
          className={styles.modalForm}
          onSubmit={(event) => {
            event.preventDefault();
            if (!answer.trim()) return;
            onAnswer(answer.trim());
          }}
        >
          <span className={styles.prompt} aria-hidden="true">
            &gt;
          </span>
          <input
            ref={inputRef}
            className={styles.modalInput}
            value={answer}
            onChange={(event) => setAnswer(event.target.value)}
            placeholder="비교 행동까지 함께 봐줘"
            aria-label="확인 답변"
          />
          <button type="submit" className={styles.modalGo} disabled={!answer.trim()}>
            계속
          </button>
        </form>

        <div className={styles.quick}>
          {["검색만 볼게요", "비교 행동까지 함께"].map((option) => (
            <button key={option} type="button" onClick={() => onAnswer(option)}>
              {option}
            </button>
          ))}
        </div>
      </div>
      </div>
    </Overlay>
  );
}
