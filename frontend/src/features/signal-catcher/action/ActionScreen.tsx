"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { Overlay } from "../Overlay";
import { TIMELAPSE_SERIES } from "../state/action-mock";
import type {
  ActionMockup,
  ActionPlan,
  ActionStage,
  Experiment,
} from "../state/types";

import styles from "./action.module.css";

/** 진입 연출 길이. 항목이 하나씩 그려지는 시간까지 포함한다. */
const INTRO_MS = 1900;
/** 타임랩스 한 프레임. */
const FRAME_MS = 420;

interface ActionScreenProps {
  plan: ActionPlan;
  experiment: Experiment | undefined;
  conflict: Experiment | undefined;
  onApply: () => void;
  onAdvance: (days: number) => void;
  onComplete: (hits: number, total: number) => void;
  onOpenNext: () => void;
  onBack: () => void;
}

function stageOf(experiment: Experiment | undefined): ActionStage {
  if (!experiment) return "preview";
  if (experiment.status === "done") return "report";
  return "watching";
}

/** 시작값에서 목표까지 얼마나 왔는지. 값이 작아져야 좋은 지표도 같은 방향으로 읽는다. */
function progress(from: number, now: number, goal: number): number {
  if (from === goal) return 1;
  return Math.min(1, Math.max(0, (now - from) / (goal - from)));
}

export function ActionScreen({
  plan,
  experiment,
  conflict,
  onApply,
  onAdvance,
  onComplete,
  onOpenNext,
  onBack,
}: ActionScreenProps) {
  const [stage, setStage] = useState<ActionStage>(() => stageOf(experiment));
  const [intro, setIntro] = useState(true);
  const [askConflict, setAskConflict] = useState(false);
  const [frame, setFrame] = useState(0);
  const applied = useRef(false);

  useEffect(() => {
    setStage(stageOf(experiment));
  }, [experiment]);

  /*
   * 무엇이 바뀌는지는 이 화면에 들어온 순간 보여준다.
   * 버튼이 연출까지 맡으면 이미 적용을 결정한 사용자에게 대기시간을 강요하게 된다.
   */
  useEffect(() => {
    if (stage !== "preview" || !intro) return;
    const timer = setTimeout(() => setIntro(false), INTRO_MS);
    return () => clearTimeout(timer);
  }, [stage, intro]);

  useEffect(() => {
    if (stage !== "watching" || frame === 0) return;
    if (frame >= plan.timelapse.length - 1) {
      const done = setTimeout(() => {
        const hits = plan.outcomes.filter((o) => o.verdict === "hit").length;
        onComplete(hits, plan.outcomes.length);
        setStage("report");
      }, 700);
      return () => clearTimeout(done);
    }
    const timer = setTimeout(() => {
      setFrame((prev) => prev + 1);
      onAdvance(frame + 1);
    }, FRAME_MS);
    return () => clearTimeout(timer);
  }, [stage, frame, plan, onAdvance, onComplete]);

  /* 단계가 넘어가면 내용이 통째로 바뀌므로 처음부터 보게 한다. */
  useEffect(() => {
    if (stage === "preview") return;
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, [stage]);

  function apply() {
    if (conflict) {
      setAskConflict(true);
      return;
    }
    startApply();
  }

  function startApply() {
    if (applied.current) return;
    applied.current = true;
    setAskConflict(false);
    onApply();
    setStage("watching");
  }

  const gains = plan.predictions.filter((p) => p.direction === "gain");
  const risks = plan.predictions.filter((p) => p.direction === "risk");
  const hits = plan.outcomes.filter((o) => o.verdict === "hit").length;
  const misses = plan.outcomes.length - hits;
  const point = plan.timelapse[Math.min(frame, plan.timelapse.length - 1)];
  const start = plan.timelapse[0];

  return (
    <div className={styles.screen}>
      <div className={styles.inner}>
        <button type="button" className={styles.back} onClick={onBack}>
          <span aria-hidden="true">←</span> 분석 결과로
        </button>

        <header className={styles.head}>
          <p className={styles.eyebrow}>
            {stage === "report" ? "실험 결과" : stage === "watching" ? "관찰 중" : "적용 미리보기"}
          </p>
          <h1>{plan.title}</h1>
          <p className={styles.target}>
            대상 {plan.segmentLabel} · {plan.segmentSize.toLocaleString("ko-KR")}명
          </p>
        </header>

        {stage === "preview" ? (
          <>
            <section className={styles.compare}>
              <MockupPanel mockup={plan.asIs} state="asIs" magic={intro} />
              <MockupPanel mockup={plan.toBe} state="toBe" magic={intro} />
            </section>
            <p className={styles.changeNote}>
              입력창 바로 위에 <mark>상황형 추천검색어 3줄</mark>이 새로 노출됩니다.
              <span>
                실제 화면 구조를 따른 와이어프레임입니다.
                {intro ? null : (
                  <button type="button" className={styles.replay} onClick={() => setIntro(true)}>
                    다시 보기
                  </button>
                )}
              </span>
            </p>

            <section>
              <h2 className={styles.blockTitle}>예상되는 변화</h2>
              <table className={styles.sheet}>
                <thead>
                  <tr>
                    <th scope="col">지표</th>
                    <th scope="col">지금</th>
                    <th scope="col">예상</th>
                    <th scope="col" className={styles.numCol}>
                      변화
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {gains.map((item) => (
                    <tr key={item.predictionId}>
                      <th scope="row">
                        {item.label}
                        <small>{item.reason}</small>
                      </th>
                      <td className={styles.from}>{item.from}</td>
                      <td className={styles.to}>{item.to}</td>
                      <td className={styles.numCol}>{item.delta}</td>
                    </tr>
                  ))}
                </tbody>
                <tbody className={styles.riskBody}>
                  <tr className={styles.riskHead}>
                    <th scope="row" colSpan={4}>
                      이건 나빠질 수 있어요
                    </th>
                  </tr>
                  {risks.map((item) => (
                    <tr key={item.predictionId}>
                      <th scope="row">
                        {item.label}
                        <small>{item.reason}</small>
                      </th>
                      <td className={styles.from}>{item.from}</td>
                      <td className={styles.to}>{item.to}</td>
                      <td className={styles.numCol}>{item.delta}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className={styles.footnote}>
                최근 14일간 이 고객 {plan.segmentSize.toLocaleString("ko-KR")}명의 행동에서
                계산한 값이에요. 위험 항목도 같은 데이터로 계산했습니다.
              </p>
            </section>

            <div className={styles.applyRow}>
              <span className={styles.applyNote}>
                적용하면 {plan.observeDays}일간 지켜보고 결과를 알려드려요
              </span>
              <button type="button" className={styles.applyBtn} onClick={apply}>
                ✨ {plan.applyLabel}
              </button>
            </div>
          </>
        ) : null}

        {stage === "watching" ? (
          <section className={styles.watch}>
            <div className={styles.timeline}>
              <span className={styles.timelineDay}>
                {point.date} · {point.day}일째
              </span>
              <div className={styles.timelineBar}>
                <i style={{ width: `${(point.day / plan.observeDays) * 100}%` }} />
              </div>
              <span className={styles.timelineEnd}>{plan.observeDays}일</span>
            </div>

            <ul className={styles.tracks}>
              {TIMELAPSE_SERIES.map((series) => {
                const pred = gains.find((g) => g.predictionId === series.key);
                const from = start.values[series.key];
                const now = point.values[series.key];
                const goal = Number.parseFloat(pred?.to ?? String(from));
                const ratio = progress(from, now, goal);
                return (
                  <li key={series.key}>
                    <p className={styles.trackLabel}>{series.label}</p>
                    <p className={styles.trackNow}>
                      {now.toLocaleString("ko-KR")}
                      <small>{series.unit}</small>
                    </p>
                    <div className={styles.track}>
                      <span className={styles.trackFill} style={{ width: `${ratio * 100}%` }} />
                      <span className={styles.trackPin} style={{ left: `${ratio * 100}%` }} />
                    </div>
                    <p className={styles.trackEnds}>
                      <span>
                        적용 전 {from}
                        {series.unit}
                      </span>
                      <span>목표 {pred?.to ?? "—"}</span>
                    </p>
                  </li>
                );
              })}
            </ul>

            {frame === 0 ? (
              <div className={styles.ffRow}>
                <button type="button" className={styles.ffBtn} onClick={() => setFrame(1)}>
                  ⏩ {plan.observeDays}일 뒤로 감기
                </button>
                <p className={styles.footnote}>
                  실제로는 {plan.observeDays}일을 기다려야 하지만, 데모를 위해 미리 준비한
                  데이터로 감아 볼게요.
                </p>
              </div>
            ) : (
              <p className={styles.ffPlaying}>지켜보는 중…</p>
            )}
          </section>
        ) : null}

        {stage === "report" ? (
          <section className={styles.report}>
            <p className={styles.scoreLead}>
              예측 {plan.outcomes.length}개 중 <em>{hits}개</em>가 맞았어요
            </p>
            <p className={styles.footnote}>
              {plan.observeDays}일간 쌓인 실제 데이터와 하나씩 비교했어요.
              {misses > 0
                ? ` 예상과 달랐던 ${misses}개는 왜 그랬는지 아래에 정리했습니다.`
                : ""}
            </p>

            <table className={styles.sheet}>
              <thead>
                <tr>
                  <th scope="col">지표</th>
                  <th scope="col">예측</th>
                  <th scope="col">실제</th>
                  <th scope="col" className={styles.numCol}>
                    결과
                  </th>
                </tr>
              </thead>
              <tbody>
                {plan.outcomes.map((outcome) => {
                  const pred = plan.predictions.find(
                    (p) => p.predictionId === outcome.predictionId,
                  );
                  if (!pred) return null;
                  const miss = outcome.verdict === "miss";
                  return (
                    <tr key={outcome.predictionId} data-verdict={outcome.verdict}>
                      <th scope="row">
                        {pred.label}
                        {miss ? <small>{outcome.note}</small> : null}
                      </th>
                      <td className={styles.from}>{pred.delta}</td>
                      <td className={styles.to}>{outcome.actual}</td>
                      <td className={styles.numCol}>
                        <span className={styles.verdict}>
                          <i aria-hidden="true">{miss ? "✕" : "✓"}</i>
                          {miss ? "예상과 다름" : "맞음"}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>

            {plan.nextActionReason ? (
              <div className={styles.next}>
                <p className={styles.eyebrow}>예상과 달랐던 부분이 알려준 것</p>
                <p className={styles.nextReason}>{plan.nextActionReason}</p>
                <button type="button" className={styles.nextBtn} onClick={onOpenNext}>
                  분석 결과에서 이어지는 액션 보기 <span aria-hidden="true">→</span>
                </button>
              </div>
            ) : null}
          </section>
        ) : null}
      </div>

      {askConflict && conflict ? (
        <Overlay>
          <div className={styles.scrim} onMouseDown={() => setAskConflict(false)}>
            <div
              className={styles.conflict}
              role="dialog"
              aria-modal="true"
              aria-label="겹치는 실험"
              onMouseDown={(event) => event.stopPropagation()}
            >
              <p className={styles.eyebrow} data-tone="warn">
                겹치는 실험이 있어요
              </p>
              <h2>{conflict.title}</h2>
              <p className={styles.conflictBody}>
                같은 고객군(<strong>{conflict.segmentLabel}</strong>)을 이미 관찰 중이에요.
                지금 함께 적용하면 지표가 움직여도{" "}
                <strong>어느 쪽 효과인지 구분할 수 없어요.</strong>
              </p>
              <div className={styles.conflictFoot}>
                <button type="button" className={styles.ghostBtn} onClick={startApply}>
                  그래도 함께 적용
                </button>
                <button
                  type="button"
                  className={styles.applyBtn}
                  onClick={() => setAskConflict(false)}
                >
                  진행 중인 실험 먼저 보기
                </button>
              </div>
            </div>
          </div>
        </Overlay>
      ) : null}
    </div>
  );
}

interface MockupPanelProps {
  mockup: ActionMockup;
  state: "asIs" | "toBe";
  magic: boolean;
}

/** AS-IS / TO-BE 시안. 배치는 실제 AI검색 구조를 따른 와이어프레임이다. */
function MockupPanel({ mockup, state, magic }: MockupPanelProps) {
  const addedIndex = useMemo(() => {
    let seen = 0;
    return mockup.items.map((item) => (item.added ? seen++ : -1));
  }, [mockup.items]);

  return (
    <figure className={styles.mockup} data-state={state} data-magic={magic}>
      <figcaption className={styles.mockupCap}>{mockup.label}</figcaption>
      <div className={styles.mockupBody}>
        {magic && state === "toBe" ? <span className={styles.sweep} aria-hidden="true" /> : null}
        {mockup.items.map((item, index) => (
          <div
            key={`${item.kind}-${item.text}`}
            className={styles.row}
            data-kind={item.kind}
            data-added={item.added}
            style={
              item.added
                ? ({ "--order": addedIndex[index] } as React.CSSProperties)
                : undefined
            }
          >
            <span className={styles.rowText}>{item.text}</span>
            {item.sub ? <span className={styles.rowSub}>{item.sub}</span> : null}
          </div>
        ))}
      </div>
    </figure>
  );
}
