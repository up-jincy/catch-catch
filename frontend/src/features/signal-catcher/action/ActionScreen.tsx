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

/** 매직 연출 길이. 항목이 하나씩 그려지는 시간까지 포함한다. */
const MAGIC_MS = 1900;
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
  const [askConflict, setAskConflict] = useState(false);
  const [frame, setFrame] = useState(0);
  const applied = useRef(false);
  const compareRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (stage === "applying") return;
    setStage(stageOf(experiment));
  }, [experiment, stage]);

  /*
   * 적용 버튼은 화면 아래에 있어 연출이 시야 밖에서 일어날 수 있다.
   * 이미 다 보이면 건드리지 않고, 벗어난 만큼만 옮긴다.
   * scrollIntoView 는 재렌더와 겹치면 엉뚱한 위치로 튄다.
   */
  useEffect(() => {
    if (stage !== "applying") return;
    const el = compareRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    if (rect.top >= 0 && rect.bottom <= window.innerHeight) return;
    window.scrollTo({ top: window.scrollY + rect.top - 80, behavior: "smooth" });
  }, [stage]);

  useEffect(() => {
    if (stage !== "applying") return;
    const timer = setTimeout(() => {
      onApply();
      setStage("watching");
    }, MAGIC_MS);
    return () => clearTimeout(timer);
  }, [stage, onApply]);

  /* 단계가 넘어가면 내용이 통째로 바뀌므로 처음부터 보게 한다. */
  useEffect(() => {
    if (stage !== "watching" && stage !== "report") return;
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, [stage]);

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
    setStage("applying");
  }

  const gains = plan.predictions.filter((p) => p.direction === "gain");
  const risks = plan.predictions.filter((p) => p.direction === "risk");
  const hits = plan.outcomes.filter((o) => o.verdict === "hit").length;
  const point = plan.timelapse[Math.min(frame, plan.timelapse.length - 1)];
  const start = plan.timelapse[0];
  const preview = stage === "preview" || stage === "applying";

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

        {preview ? (
          <>
            <section className={styles.compare} ref={compareRef}>
              <MockupPanel mockup={plan.asIs} state="asIs" magic={stage === "applying"} />
              <MockupPanel mockup={plan.toBe} state="toBe" magic={stage === "applying"} />
            </section>
            <p className={styles.changeNote}>
              입력창 바로 위에 <mark>상황형 추천검색어 3줄</mark>이 새로 노출됩니다.
              <span>실제 화면 구조를 따른 와이어프레임입니다.</span>
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
                      이런 위험이 있어요
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
                좋아지는 것만 싣지 않습니다. 나빠질 수 있는 부분까지 함께 예측해야 적용
                여부를 스스로 판단할 수 있습니다.
              </p>
            </section>

            <div className={styles.applyRow}>
              <span className={styles.applyNote}>
                적용 후 {plan.observeDays}일간 예측이 맞았는지 지켜봅니다
              </span>
              <button
                type="button"
                className={styles.applyBtn}
                onClick={apply}
                disabled={stage === "applying"}
              >
                {stage === "applying" ? "바꾸는 중…" : `✨ ${plan.applyLabel}`}
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

            <table className={styles.sheet}>
              <thead>
                <tr>
                  <th scope="col">지표</th>
                  <th scope="col">적용 전</th>
                  <th scope="col">지금</th>
                  <th scope="col" className={styles.numCol}>
                    목표
                  </th>
                </tr>
              </thead>
              <tbody>
                {TIMELAPSE_SERIES.map((series) => {
                  const pred = gains.find((g) => g.predictionId === series.key);
                  return (
                    <tr key={series.key}>
                      <th scope="row">{series.label}</th>
                      <td className={styles.from}>
                        {start.values[series.key]}
                        {series.unit}
                      </td>
                      <td className={styles.to}>
                        {point.values[series.key]}
                        {series.unit}
                      </td>
                      <td className={styles.numCol}>{pred?.to ?? "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>

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
              빗나간 예측도 그대로 싣습니다. 전부 맞은 리포트는 채점하지 않은 리포트와
              구분되지 않습니다.
            </p>

            <table className={styles.sheet}>
              <thead>
                <tr>
                  <th scope="col">지표</th>
                  <th scope="col">예측</th>
                  <th scope="col">실제</th>
                  <th scope="col" className={styles.numCol}>
                    판정
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
                        <span className={styles.verdict}>{miss ? "빗나감" : "적중"}</span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>

            {plan.nextActionReason ? (
              <div className={styles.next}>
                <p className={styles.eyebrow}>빗나간 예측이 알려준 것</p>
                <p className={styles.nextReason}>{plan.nextActionReason}</p>
                <button type="button" className={styles.nextBtn} onClick={onOpenNext}>
                  다음 액션 보기 <span aria-hidden="true">→</span>
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

/** AS-IS / TO-BE 시안. 화면처럼 보이도록 검색바와 결과 목록 형태를 유지한다. */
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
