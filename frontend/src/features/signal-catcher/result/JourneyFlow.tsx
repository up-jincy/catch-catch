"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import type { CatchReport, DepthMode, JourneyNode } from "../state/types";

import styles from "./journey.module.css";

const PLAY_MS = 780;

function timeLabel(iso: string): string {
  const date = new Date(iso);
  return `${date.getMonth() + 1}/${date.getDate()}`;
}

function clockLabel(iso: string): string {
  const date = new Date(iso);
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

interface JourneyFlowProps {
  report: CatchReport;
  depth: DepthMode;
  onOpenEvidence: (evidenceId: string) => void;
}

/**
 * 스윔레인 격자 위를 선 하나가 시간순으로 관통한다.
 * 레인을 넘나드는 지그재그 자체가 cross-channel 이라는 주장의 시각적 증거다.
 */
export function JourneyFlow({ report, depth, onOpenEvidence }: JourneyFlowProps) {
  const { journey, lanes } = report;
  const [cursor, setCursor] = useState(journey.length);
  const [playing, setPlaying] = useState(false);
  const [selectedId, setSelectedId] = useState<string>(journey[0]?.event_id ?? "");

  const laneIndex = useMemo(
    () => new Map(lanes.map((lane, index) => [lane.id, index] as const)),
    [lanes],
  );

  useEffect(() => {
    if (!playing) return;
    if (cursor >= journey.length) {
      setPlaying(false);
      return;
    }
    const timer = setTimeout(() => {
      setCursor((prev) => {
        const next = prev + 1;
        const node = journey[next - 1];
        if (node) setSelectedId(node.event_id);
        return next;
      });
    }, PLAY_MS);
    return () => clearTimeout(timer);
  }, [playing, cursor, journey]);

  function play() {
    if (cursor >= journey.length) {
      setCursor(0);
      setSelectedId(journey[0]?.event_id ?? "");
    }
    setPlaying(true);
  }

  const segments = useMemo(() => {
    const cols = journey.length;
    return journey.slice(0, -1).map((node, index) => {
      const next = journey[index + 1];
      const x1 = node.column * 100 + 50;
      const x2 = next.column * 100 + 50;
      const y1 = (laneIndex.get(node.lane) ?? 0) * 100 + 50;
      const y2 = (laneIndex.get(next.lane) ?? 0) * 100 + 50;
      const mid = (x1 + x2) / 2;
      return {
        key: `${node.event_id}-${next.event_id}`,
        d: `M${x1} ${y1} L${mid} ${y1} L${mid} ${y2} L${x2} ${y2}`,
        cols,
      };
    });
  }, [journey, laneIndex]);

  const selected = journey.find((node) => node.event_id === selectedId) ?? journey[0];
  const viewWidth = journey.length * 100;
  const viewHeight = lanes.length * 100;

  return (
    <div className={styles.wrap}>
      <div className={styles.controls}>
        <button
          type="button"
          className={styles.play}
          onClick={() => (playing ? setPlaying(false) : play())}
          aria-label={playing ? "재생 멈추기" : "저니 재생하기"}
        >
          {playing ? "❚❚" : "▶"}
        </button>
        <input
          className={styles.scrub}
          type="range"
          min={0}
          max={journey.length}
          value={cursor}
          aria-label="저니 진행"
          onChange={(event) => {
            setPlaying(false);
            const next = Number(event.target.value);
            setCursor(next);
            const node = journey[Math.max(0, next - 1)];
            if (node) setSelectedId(node.event_id);
          }}
        />
        <span className={styles.count}>
          {Math.min(cursor, journey.length)} / {journey.length}
        </span>
      </div>

      <div className={styles.board}>
        <div className={styles.frame}>
          <div className={styles.track}>
          <div
            className={styles.grid}
            style={{ "--cols": journey.length, "--lanes": lanes.length } as React.CSSProperties}
          >
            <div className={styles.corner} />
            {journey.map((node, index) => (
              <div key={`d-${node.event_id}`} className={styles.date} data-on={index < cursor}>
                <strong>{timeLabel(node.occurred_at)}</strong>
                <small>{clockLabel(node.occurred_at)}</small>
              </div>
            ))}

            <svg
              className={styles.link}
              viewBox={`0 0 ${viewWidth} ${viewHeight}`}
              preserveAspectRatio="none"
              aria-hidden="true"
            >
              {segments.map((segment, index) => (
                <path
                  key={segment.key}
                  d={segment.d}
                  pathLength={100}
                  strokeDasharray={100}
                  strokeDashoffset={index < cursor - 1 ? 0 : 100}
                />
              ))}
            </svg>

            {lanes.map((lane) => (
              <div key={lane.id} className={styles.laneRow}>
                <div className={styles.laneName}>{lane.label}</div>
                {journey.map((node, index) => {
                  const here = node.lane === lane.id;
                  if (!here) {
                    return <div key={`${lane.id}-${node.event_id}`} className={styles.cell} />;
                  }
                  const revealed = index < cursor;
                  return (
                    <div key={`${lane.id}-${node.event_id}`} className={styles.cell}>
                      <button
                        type="button"
                        className={styles.node}
                        data-tone={node.tone}
                        data-on={revealed}
                        data-selected={node.event_id === selectedId}
                        aria-label={`${lane.label} · ${node.action} · ${node.text}`}
                        onClick={() => {
                          setPlaying(false);
                          setSelectedId(node.event_id);
                          if (index >= cursor) setCursor(index + 1);
                        }}
                      >
                        <span className={styles.dot} />
                        <span className={styles.nodeLabel}>{node.action}</span>
                      </button>
                    </div>
                  );
                })}
              </div>
            ))}
          </div>

          <div className={styles.intensity} aria-hidden="true">
            <div className={styles.intensityHead}>
              <span>시그널 강도</span>
              {depth === "analyst" ? <span>signals[].contribution</span> : null}
            </div>
            <svg viewBox={`0 0 ${viewWidth} 46`} preserveAspectRatio="none">
              <path className={styles.intensityFill} d={areaPath(journey)} />
              <path className={styles.intensityLine} d={linePath(journey)} />
            </svg>
          </div>
          </div>
        </div>

        <aside className={styles.detail} aria-label="선택한 행동의 근거">
          {selected ? (
            <>
              <p className={styles.detailWhen}>
                {timeLabel(selected.occurred_at)} {clockLabel(selected.occurred_at)}
              </p>
              <h4 className={styles.detailTitle}>{selected.text}</h4>
              <dl className={styles.detailMeta}>
                <div>
                  <dt>데이터 원천</dt>
                  <dd>{lanes.find((lane) => lane.id === selected.lane)?.label}</dd>
                </div>
                <div>
                  <dt>결과</dt>
                  <dd>{selected.outcome}</dd>
                </div>
                {depth === "analyst" ? (
                  <>
                    <div>
                      <dt>source_id</dt>
                      <dd className={styles.mono}>{selected.source_id}</dd>
                    </div>
                    <div>
                      <dt>event_type</dt>
                      <dd className={styles.mono}>{selected.event_type}</dd>
                    </div>
                  </>
                ) : null}
              </dl>
              <p className={styles.detailInsight}>
                <span>AI 해석</span>
                {selected.insight}
              </p>
              <button
                type="button"
                className={styles.detailEvidence}
                onClick={() => onOpenEvidence(selected.evidence_id)}
              >
                원본 근거 보기
                <em>{selected.evidence_id}</em>
              </button>
            </>
          ) : null}
        </aside>
      </div>
    </div>
  );
}

function linePath(journey: JourneyNode[]): string {
  return journey
    .map((node, index) => {
      const x = node.column * 100 + 50;
      const y = 44 - node.intensity * 40;
      return `${index === 0 ? "M" : "L"}${x} ${y.toFixed(1)}`;
    })
    .join(" ");
}

/** 채움은 첫 노드에서 마지막 노드까지만. 격자 밖으로 흘러 삼각형이 되지 않게 한다. */
function areaPath(journey: JourneyNode[]): string {
  if (!journey.length) return "";
  const first = journey[0].column * 100 + 50;
  const last = journey[journey.length - 1].column * 100 + 50;
  return `M${first} 46 ${linePath(journey).replace(/^M/, "L")} L${last} 46 Z`;
}
