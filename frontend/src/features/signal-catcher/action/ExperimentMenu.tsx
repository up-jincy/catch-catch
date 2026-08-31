"use client";

import { useEffect, useRef, useState } from "react";

import type { Experiment } from "../state/types";

import styles from "./experiment-menu.module.css";

interface ExperimentMenuProps {
  experiments: Experiment[];
  onOpen: (actionId: string) => void;
}

/**
 * 진행 중인 실험으로 돌아가는 전역 진입점.
 *
 * 상단 바는 phase 와 무관하게 항상 렌더되므로 첫 화면·결과·검증 기록
 * 어디서 나가든 같은 자리에서 실험으로 복귀할 수 있다.
 * 별도 목록 화면을 두지 않는 이유다.
 */
export function ExperimentMenu({ experiments, onOpen }: ExperimentMenuProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    function onPointer(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onPointer);
    };
  }, [open]);

  // 실험이 없으면 자리도 차지하지 않는다.
  if (!experiments.length) return null;

  const watching = experiments.filter((item) => item.status === "watching");
  const done = experiments.filter((item) => item.status === "done");

  return (
    <div className={styles.root} ref={rootRef}>
      <button
        type="button"
        className={styles.badge}
        data-live={watching.length > 0}
        aria-expanded={open}
        onClick={() => setOpen((prev) => !prev)}
      >
        <span className={styles.dot} aria-hidden="true" />
        {watching.length ? "관찰 중" : "실험"} {experiments.length}
      </button>

      {open ? (
        <div className={styles.panel} role="dialog" aria-label="실험 목록">
          {watching.length ? (
            <section>
              <p className={styles.groupTitle}>관찰 중</p>
              <ul className={styles.list}>
                {watching.map((item) => (
                  <li key={item.actionId}>
                    <button
                      type="button"
                      onClick={() => {
                        setOpen(false);
                        onOpen(item.actionId);
                      }}
                    >
                      <strong>{item.title}</strong>
                      <small>{item.segmentLabel}</small>
                      <span className={styles.progress}>
                        <i
                          style={{
                            width: `${(item.elapsedDays / item.observeDays) * 100}%`,
                          }}
                        />
                      </span>
                      <span className={styles.days}>
                        {item.elapsedDays}일째 · {item.observeDays - item.elapsedDays}일 남음
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          {done.length ? (
            <section>
              <p className={styles.groupTitle}>완료</p>
              <ul className={styles.list}>
                {done.map((item) => (
                  <li key={item.actionId}>
                    <button
                      type="button"
                      onClick={() => {
                        setOpen(false);
                        onOpen(item.actionId);
                      }}
                    >
                      <strong>{item.title}</strong>
                      <small>{item.segmentLabel}</small>
                      <span className={styles.score}>
                        예측 {item.total}개 중 <b>{item.hits}개</b> 적중
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
