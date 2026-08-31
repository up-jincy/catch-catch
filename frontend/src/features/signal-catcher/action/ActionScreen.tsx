"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { Sparkle } from "../brand/Brand";
import { Overlay } from "../Overlay";
import { TIMELAPSE_SERIES } from "../state/action-mock";
import type {
  ActionMockup,
  ActionPlan,
  ActionStage,
  Experiment,
  Prediction,
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

/** 스파크라인 좌표계. 값이 오르내리는 방향은 그대로 두고 여백만 준다. */
function scale(values: number[], goal: number) {
  const all = [...values, goal];
  const lo = Math.min(...all);
  const hi = Math.max(...all);
  const pad = (hi - lo) * 0.18 || 1;
  return { lo: lo - pad, hi: hi + pad };
}

function sparkPath(values: number[], lo: number, hi: number, w: number, h: number): string {
  if (values.length < 2) return "";
  return values
    .map((value, index) => {
      const x = (index / (values.length - 1)) * w;
      const y = h - ((value - lo) / (hi - lo)) * h;
      return `${index === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(" ");
}

function goalY(goal: number, lo: number, hi: number, h: number): number {
  return h - ((goal - lo) / (hi - lo)) * h;
}

const SPARK_W = 220;
const SPARK_H = 56;

function formatDelta(from: number, now: number, unit: string): string {
  const diff = now - from;
  const sign = diff > 0 ? "+" : "";
  return `${sign}${Number(diff.toFixed(1))}${unit}`;
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
                <Sparkle size={15} />
                {plan.applyLabel}
              </button>
            </div>
          </>
        ) : null}

        {stage === "watching" ? (
          <section className={styles.watch}>
            <p className={styles.timeline}>
              적용 후 <strong>{point.day}일째</strong>
              <span>
                {start.date} → {point.date} · 관찰 {plan.observeDays}일
              </span>
            </p>

            <MetricSparks plan={plan} gains={gains} upto={frame} />

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

            <MetricSparks plan={plan} gains={gains} upto={plan.timelapse.length - 1} />

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
              <aside className={styles.insight}>
                <p className={styles.insightTag}>
                  <span className={styles.insightMark}>
                    <Sparkle size={13} />
                  </span>
                  AI 인사이트
                </p>
                <p className={styles.insightBody}>{plan.nextActionReason}</p>
                <button type="button" className={styles.insightBtn} onClick={onOpenNext}>
                  분석 결과에서 이어지는 액션 보기 <span aria-hidden="true">→</span>
                </button>
              </aside>
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

interface MetricSparksProps {
  plan: ActionPlan;
  gains: Prediction[];
  /** 어느 프레임까지 그릴지. 관찰 중엔 재생 위치, 리포트에선 끝까지. */
  upto: number;
}

/**
 * 큰 숫자 · 델타 · 스파크라인.
 * 관찰 중과 리포트가 함께 쓴다. 리포트에서는 목표 점선과 실제 종착점의 거리가
 * 곧 채점 결과라 표보다 먼저 읽힌다.
 */
function MetricSparks({ plan, gains, upto }: MetricSparksProps) {
  const start = plan.timelapse[0];
  const point = plan.timelapse[Math.min(upto, plan.timelapse.length - 1)];

  return (
    <ul className={styles.metrics}>
      {TIMELAPSE_SERIES.map((series) => {
        const pred = gains.find((g) => g.predictionId === series.key);
        const from = start.values[series.key];
        const now = point.values[series.key];
        const goal = Number.parseFloat(pred?.to ?? String(from));
        const full = plan.timelapse.map((f) => f.values[series.key]);
        const shown = plan.timelapse.slice(0, upto + 1).map((f) => f.values[series.key]);
        const { lo, hi } = scale(full, goal);
        const gy = goalY(goal, lo, hi, SPARK_H);
        const lastX = ((shown.length - 1) / (full.length - 1)) * SPARK_W;
        const lastY = SPARK_H - ((now - lo) / (hi - lo)) * SPARK_H;
        return (
          <li key={series.key}>
            <p className={styles.metricLabel}>{series.label}</p>
            <p className={styles.metricNow}>
              {now.toLocaleString("ko-KR")}
              <small>{series.unit}</small>
              <em>{formatDelta(from, now, series.unit)}</em>
            </p>
            <svg
              className={styles.spark}
              viewBox={`0 0 ${SPARK_W} ${SPARK_H}`}
              preserveAspectRatio="none"
              aria-hidden="true"
            >
              <line className={styles.sparkGoal} x1="0" y1={gy} x2={SPARK_W} y2={gy} />
              <path
                className={styles.sparkLine}
                d={sparkPath(shown, lo, hi, SPARK_W, SPARK_H)}
              />
              {shown.length > 1 ? (
                <circle className={styles.sparkDot} cx={lastX} cy={lastY} r="3.5" />
              ) : null}
            </svg>
            <p className={styles.metricEnds}>
              <span>
                적용 전 {from}
                {series.unit}
              </span>
              <span className={styles.metricGoal}>목표 {pred?.to ?? "—"}</span>
            </p>
          </li>
        );
      })}
    </ul>
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
