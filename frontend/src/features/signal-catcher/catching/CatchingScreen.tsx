"use client";

import { HeartBurst, HeartbeatSteps } from "../brand/Brand";
import { STAGE_DURATIONS } from "../state/use-catch-session";
import type { CatchSession, StageKey } from "../state/types";

import { ClarificationModal } from "./ClarificationModal";
import styles from "./catching.module.css";

/**
 * 화면에 남겨 둘 최근 진행 문장 수.
 * 위쪽 줄은 마스크로 흐려지며 사라지므로 실제로 읽히는 건 아래 두세 줄이다.
 */
const LOG_WINDOW = 6;

interface CatchingScreenProps {
  session: CatchSession;
  bursting: boolean;
  flatline: boolean;
  frozen: boolean;
  /** 진행 속도 배수. 박동이 그려지는 시간도 같이 늘어난다. */
  speed: number;
  burstMark: "heart" | "lens";
  log: { text: string; meta: string; stage: StageKey }[];
  onAnswerClarification: (answer: string) => void;
  onRetry: () => void;
  onGiveUp: () => void;
}

export function CatchingScreen({
  session,
  bursting,
  flatline,
  frozen,
  speed,
  burstMark,
  log,
  onAnswerClarification,
  onRetry,
  onGiveUp,
}: CatchingScreenProps) {
  const failed = session.outcome === "failed";
  const halted = flatline || failed || Boolean(session.clarification);
  const active = session.stages.find((stage) => stage.status === "active");
  const recent = log.slice(-LOG_WINDOW);
  const shortOf = (key: StageKey) =>
    session.stages.find((stage) => stage.key === key)?.short ?? "";

  return (
    <div className={styles.screen}>
      {bursting ? <HeartBurst mark={burstMark} /> : null}

      <p className={styles.context}>{session.question}</p>

      <div className={styles.stage}>
        <HeartbeatSteps
          stages={session.stages}
          activeDurationMs={(active ? STAGE_DURATIONS[active.key] : 1200) * speed}
          halted={halted}
          frozen={frozen}
        />

        {failed ? (
          <div className={styles.failure} role="alert">
            <p className={styles.failureTitle}>
              <span className={styles.failureBadge}>연결 끊김</span>
              신호가 끊겼어요
            </p>
            <p className={styles.failureReason}>{session.failureReason}</p>
            <p className={styles.failureNote}>
              질문과 조건은 그대로 두었어요. 다시 캐치하면 멈춘 지점부터 이어서 분석합니다.
            </p>
            <div className={styles.failureActions}>
              <button type="button" className={styles.failureRetry} onClick={onRetry}>
                다시 캐치하기
              </button>
              <button type="button" className={styles.failureGhost} onClick={onGiveUp}>
                질문 바꾸기
              </button>
            </div>
          </div>
        ) : bursting ? null : (
          <div className={styles.logViewport}>
            {/*
              문장이 도착할 때만 스택이 한 줄씩 밀려 올라간다.
              등속으로 흘리면 읽는 중인 문장이 지나가 버리므로 계단식으로 움직인다.
            */}
            <ol
              className={styles.log}
              key={recent.at(-1)?.text ?? "empty"}
              aria-live="polite"
            >
            {halted ? (
              <li className={styles.logNow} data-halted="true">
                <span className={styles.logStage}>멈춤</span>
                {flatline ? "신호가 끊겼어요" : "조금만 더 알려주세요"}
              </li>
            ) : (
              recent.map((entry, index) => {
                const now = index === recent.length - 1;
                return (
                  <li key={entry.text} className={now ? styles.logNow : styles.logPast}>
                    <span className={styles.logStage}>
                      {now ? shortOf(entry.stage) : "✓"}
                    </span>
                    {entry.text}
                  </li>
                );
              })
            )}
            </ol>
          </div>
        )}
      </div>

      {session.clarification ? (
        <ClarificationModal
          prompt={session.clarification}
          onAnswer={onAnswerClarification}
        />
      ) : null}
    </div>
  );
}
