"use client";

import { useEffect, useRef, useState } from "react";

import styles from "./ask.module.css";

const TYPE_MS = 55;
const ERASE_MS = 24;
const HOLD_MS = 1900;

interface TypewriterPlaceholderProps {
  phrases: readonly string[];
  /** 사용자가 한 글자라도 입력하면 즉시 멈춘다. */
  paused: boolean;
}

/**
 * 실제 placeholder 속성 대신 오버레이로 그린다.
 * 속성값을 계속 바꾸면 스크린리더가 매 글자를 다시 읽기 때문이다.
 */
export function TypewriterPlaceholder({ phrases, paused }: TypewriterPlaceholderProps) {
  const [text, setText] = useState("");
  const [index, setIndex] = useState(0);
  const erasing = useRef(false);

  useEffect(() => {
    if (paused) return;

    const phrase = phrases[index % phrases.length];

    if (!erasing.current && text === phrase) {
      const hold = setTimeout(() => {
        erasing.current = true;
        setText(phrase.slice(0, phrase.length - 1));
      }, HOLD_MS);
      return () => clearTimeout(hold);
    }

    if (erasing.current && text === "") {
      erasing.current = false;
      setIndex((prev) => prev + 1);
      return;
    }

    const step = setTimeout(
      () => {
        setText((prev) =>
          erasing.current ? prev.slice(0, prev.length - 1) : phrase.slice(0, prev.length + 1),
        );
      },
      erasing.current ? ERASE_MS : TYPE_MS,
    );

    return () => clearTimeout(step);
  }, [text, index, phrases, paused]);

  if (paused) return null;

  return (
    <span className={styles.ghost} aria-hidden="true">
      {text}
      <i className={styles.caret} />
    </span>
  );
}
