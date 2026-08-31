"use client";

import { useEffect, useMemo, useRef, useState } from "react";

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

  // 실험 상태가 밖에서 바뀌면(복원 등) 단계를 맞춘다.
  useEffect(() => {
    if (stage === "applying") return;
    setStage(stageOf(experiment));
  }, [experiment, stage]);

  // 매직 연출이 끝나면 관찰 상태로 넘어간다.
  useEffect(() => {
    if (stage !== "applying") return;
    const timer = setTimeout(() => {
      onApply();
      setStage("watching");
    }, MAGIC_MS);
    return () => clearTimeout(timer);
  }, [stage, onApply]);

  // 타임랩스 재생.
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

  return (
    <div className={styles.screen}>
      <div className={styles.inner}>
        <button type="button" className={styles.back} onClick={onBack}>
          <span aria-hidden="true">←</span> 분석 결과로
        </button>

        <header className={styles.head}>
          <p className={styles.tag}>
            {stage === "report" ? "실험 결과" : stage === "watching" ? "관찰 중" : "적용하면 어떻게 될까요"}
          </p>
          <h1>{plan.title}</h1>
          <p className={styles.target}>
            대상 <em>{plan.segmentLabel}</em> · {plan.segmentSize.toLocaleString("ko-KR")}명
          </p>
        </header>

        {stage === "preview" || stage === "applying" ? (
          <>
            <section className={styles.compare}>
              <MockupPanel mockup={plan.asIs} state="asIs" magic={stage === "applying"} />
              <span className={styles.arrow} aria-hidden="true">→</span>
              <MockupPanel mockup={plan.toBe} state="toBe" magic={stage === "applying"} />
            </section>

            <section className={styles.predict}>
              <div>
                <h2 className={styles.blockTitle}>좋아질 것</h2>
                <ul className={styles.predictions}>
                  {gains.map((item) => (
                    <PredictionRow key={item.predictionId} item={item} />
                  ))}
                </ul>
              </div>
              <div>
                <h2 className={styles.blockTitle} data-tone="risk">
                  나빠질 수 있는 것
                </h2>
                <ul className={styles.predictions}>
                  {risks.map((item) => (
                    <PredictionRow key={item.predictionId} item={item} />
                  ))}
                </ul>
              </div>
            </section>

            <p className={styles.honest}>
              좋아지는 것만 싣지 않습니다. 나빠질 수 있는 부분까지 함께 예측해야
              적용 여부를 스스로 판단할 수 있습니다.
            </p>

            <div className={styles.applyRow}>
              <button
                type="button"
                className={styles.applyBtn}
                onClick={apply}
                disabled={stage === "applying"}
              >
                {stage === "applying" ? "바꾸는 중…" : `✨ ${plan.applyLabel}`}
              </button>
              <span className={styles.applyNote}>
                적용 후 {plan.observeDays}일간 예측이 맞았는지 지켜봅니다
              </span>
            </div>
          </>
        ) : null}

        {stage === "watching" ? (
          <section className={styles.watch}>
            <p className={styles.watchLead}>
              적용했어요. <strong>{plan.observeDays}일간 지켜볼게요.</strong>
            </p>

            <div className={styles.timeline}>
              <span className={styles.timelineDay}>
                {point.date} · {point.day}일째
              </span>
              <div className={styles.timelineBar}>
                <i style={{ width: `${(point.day / plan.observeDays) * 100}%` }} />
              </div>
            </div>

            <dl className={styles.series}>
              {TIMELAPSE_SERIES.map((series) => {
                const value = point.values[series.key];
                const ratio = (value - series.min) / (series.max - series.min);
                return (
                  <div key={series.key}>
                    <dt>{series.label}</dt>
                    <dd>
                      {value.toLocaleString("ko-KR")}
                      <small>{series.unit}</small>
                    </dd>
                    <span className={styles.spark}>
                      <i style={{ height: `${Math.max(6, ratio * 100)}%` }} />
                    </span>
                  </div>
                );
              })}
            </dl>

            {frame === 0 ? (
              <div className={styles.ffRow}>
                <button type="button" className={styles.ffBtn} onClick={() => setFrame(1)}>
                  ⏩ {plan.observeDays}일 뒤로 감기
                </button>
                <p className={styles.ffNote}>
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
            <p className={styles.scoreNote}>
              빗나간 예측도 그대로 싣습니다. 전부 맞은 리포트는 채점하지 않은 리포트와
              구분되지 않습니다.
            </p>

            <ul className={styles.outcomes}>
              {plan.outcomes.map((outcome) => {
                const pred = plan.predictions.find(
                  (p) => p.predictionId === outcome.predictionId,
                );
                if (!pred) return null;
                return (
                  <li key={outcome.predictionId} data-verdict={outcome.verdict}>
                    <div className={styles.outcomeHead}>
                      <span className={styles.outcomeLabel}>{pred.label}</span>
                      <span className={styles.outcomeVerdict}>
                        {outcome.verdict === "hit" ? "적중" : "빗나감"}
                      </span>
                    </div>
                    <div className={styles.outcomeNums}>
                      <span>
                        예측 <b>{pred.delta}</b>
                      </span>
                      <span aria-hidden="true">·</span>
                      <span>
                        실제 <b>{outcome.actual}</b>
                      </span>
                    </div>
                    <p className={styles.outcomeNote}>{outcome.note}</p>
                  </li>
                );
              })}
            </ul>

            {plan.nextActionReason ? (
              <div className={styles.next}>
                <p className={styles.nextTag}>빗나간 예측이 알려준 것</p>
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
              <p className={styles.conflictTag}>겹치는 실험이 있어요</p>
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
                  className={styles.primaryBtn}
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

function PredictionRow({ item }: { item: Prediction }) {
  return (
    <li data-direction={item.direction}>
      <div className={styles.predHead}>
        <span className={styles.predLabel}>{item.label}</span>
        <span className={styles.predDelta}>{item.delta}</span>
      </div>
      <div className={styles.predNums}>
        <span>{item.from}</span>
        <span aria-hidden="true">→</span>
        <strong>{item.to}</strong>
      </div>
      <p className={styles.predReason}>{item.reason}</p>
    </li>
  );
}

interface MockupPanelProps {
  mockup: ActionMockup;
  state: "asIs" | "toBe";
  magic: boolean;
}

/** AS-IS / TO-BE 시안. 적용 연출 중에는 빛줄기가 훑고 지나가며 새 항목이 그려진다. */
function MockupPanel({ mockup, state, magic }: MockupPanelProps) {
  const addedIndex = useMemo(() => {
    let seen = 0;
    return mockup.items.map((item) => (item.added ? seen++ : -1));
  }, [mockup.items]);

  return (
    <div className={styles.mockup} data-state={state} data-magic={magic}>
      <p className={styles.mockupLabel}>{mockup.label}</p>
      <p className={styles.mockupContext}>{mockup.context}</p>
      <div className={styles.mockupBody}>
        {magic && state === "toBe" ? <span className={styles.sweep} aria-hidden="true" /> : null}
        {mockup.items.map((item, index) => (
          <div
            key={`${item.kind}-${item.text}`}
            className={styles.mockupItem}
            data-kind={item.kind}
            data-added={item.added}
            style={
              item.added
                ? ({ "--order": addedIndex[index] } as React.CSSProperties)
                : undefined
            }
          >
            <span className={styles.mockupText}>{item.text}</span>
            {item.sub ? <span className={styles.mockupSub}>{item.sub}</span> : null}
          </div>
        ))}
      </div>
    </div>
  );
}
