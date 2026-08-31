"use client";

import { useEffect, useState } from "react";

import { Overlay } from "../Overlay";
import type { CatchAction } from "../state/types";

import styles from "./result.module.css";

type Stage = "review" | "applying" | "done";

interface ActionPreviewProps {
  action: CatchAction;
  segmentLabel: string;
  onClose: () => void;
}

/**
 * 신청서 MVP 의 Action Preview.
 * 실제 AI검색 운영 시스템에는 반영하지 않고, Agent 가 실행까지 수행하는 경험만 Mock 으로 보여준다.
 */
export function ActionPreview({ action, segmentLabel, onClose }: ActionPreviewProps) {
  const keywords = action.keywords ?? [];
  const [stage, setStage] = useState<Stage>("review");
  const [picked, setPicked] = useState<string[]>(() => keywords.map((item) => item.keyword));

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    if (stage !== "applying") return;
    const timer = setTimeout(() => setStage("done"), 1400);
    return () => clearTimeout(timer);
  }, [stage]);

  return (
    <Overlay>
      <div className={styles.scrim} onMouseDown={onClose}>
      <div
        className={styles.preview}
        role="dialog"
        aria-modal="true"
        aria-label="추천검색어 적용"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className={styles.previewHead}>
          <p className={styles.previewTag}>AI검색 · 추천검색어 설정</p>
          <h3>{stage === "done" ? "적용 완료" : action.title}</h3>
          <p className={styles.previewTarget}>
            대상 Segment <em>{segmentLabel}</em>
          </p>
        </header>

        {stage === "done" ? (
          <div className={styles.previewDone}>
            <span className={styles.previewCheck} aria-hidden="true" />
            <p>
              {picked.length}개 추천검색어를 해당 Segment에 노출하도록 설정했어요.
            </p>
            <p className={styles.previewMockNote}>
              해커톤 프로토타입이라 실제 운영 시스템에는 반영되지 않았어요.
            </p>
          </div>
        ) : (
          <>
            <ul className={styles.keywordList}>
              {keywords.map((item) => {
                const on = picked.includes(item.keyword);
                return (
                  <li key={item.keyword}>
                    <label className={styles.keyword} data-on={on}>
                      <input
                        type="checkbox"
                        checked={on}
                        disabled={stage === "applying"}
                        onChange={() =>
                          setPicked((prev) =>
                            prev.includes(item.keyword)
                              ? prev.filter((value) => value !== item.keyword)
                              : [...prev, item.keyword],
                          )
                        }
                      />
                      <span className={styles.keywordBox} aria-hidden="true" />
                      <span>
                        <strong>{item.keyword}</strong>
                        <small>{item.reason}</small>
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>

            <p className={styles.previewWhy}>{action.reason}</p>
          </>
        )}

        <footer className={styles.previewFoot}>
          {stage === "done" ? (
            <button type="button" className={styles.primaryBtn} onClick={onClose}>
              닫기
            </button>
          ) : (
            <>
              <button type="button" className={styles.ghostBtn} onClick={onClose}>
                취소
              </button>
              <button
                type="button"
                className={styles.primaryBtn}
                disabled={!picked.length || stage === "applying"}
                onClick={() => setStage("applying")}
              >
                {stage === "applying" ? "적용하는 중…" : `${picked.length}개 적용하기`}
              </button>
            </>
          )}
        </footer>
      </div>
      </div>
    </Overlay>
  );
}
