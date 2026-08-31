"use client";

import { useEffect, useRef, useState } from "react";

import type { SourceId } from "../../customer-intelligence/contracts";
import type { SourceOption } from "../state/types";

import styles from "./ask.module.css";

export interface PeriodChoice {
  key: string;
  label: string;
  startAt: string;
  endAt: string;
}

/** 데모 데이터의 마지막 적재일. 기간 칩은 이 날짜를 기준으로 계산한다. */
const DEMO_TODAY = new Date("2026-08-19T00:00:00+09:00");

function daysAgo(days: number): string {
  const date = new Date(DEMO_TODAY);
  date.setDate(date.getDate() - days);
  return date.toISOString().slice(0, 10);
}

export const PERIOD_CHOICES: PeriodChoice[] = [
  { key: "7d", label: "최근 7일", startAt: daysAgo(7), endAt: daysAgo(0) },
  { key: "14d", label: "최근 14일", startAt: daysAgo(14), endAt: daysAgo(0) },
  { key: "30d", label: "최근 30일", startAt: daysAgo(30), endAt: daysAgo(0) },
];

/** 데이터가 적재된 구간. 직접 선택은 이 범위 밖으로 나갈 수 없다. */
export const DATA_RANGE = { min: "2026-08-01", max: daysAgo(0) };

function shortLabel(iso: string): string {
  const [, month, day] = iso.split("-");
  return `${Number(month)}.${Number(day)}`;
}

/** 이 소스가 빠지면 어떤 질문도 성립하지 않아 해제할 수 없다. */
export const REQUIRED_SOURCE: SourceId = "search_history";

type Panel = "root" | "dataset" | "period";

interface ComposerMenuProps {
  sources: readonly SourceOption[];
  selected: SourceId[];
  period: PeriodChoice;
  onToggleSource: (id: SourceId) => void;
  onSelectPeriod: (choice: PeriodChoice) => void;
  onClose: () => void;
}

export function ComposerMenu({
  sources,
  selected,
  period,
  onToggleSource,
  onSelectPeriod,
  onClose,
}: ComposerMenuProps) {
  const [panel, setPanel] = useState<Panel>("root");
  const [custom, setCustom] = useState(period.key === "custom");
  const rootRef = useRef<HTMLDivElement>(null);

  function applyCustom(startAt: string, endAt: string) {
    if (!startAt || !endAt || startAt > endAt) return;
    onSelectPeriod({
      key: "custom",
      label: `${shortLabel(startAt)} – ${shortLabel(endAt)}`,
      startAt,
      endAt,
    });
  }

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      if (panel === "root") onClose();
      else setPanel("root");
    }

    function onPointer(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) onClose();
    }

    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onPointer);
    };
  }, [panel, onClose]);

  return (
    <div className={styles.menu} ref={rootRef} role="dialog" aria-label="분석 조건">
      {panel === "root" ? (
        <ul className={styles.menuList}>
          <li>
            <button type="button" onClick={() => setPanel("dataset")}>
              <span className={styles.menuIcon} aria-hidden="true">
                ▦
              </span>
              <span>
                <strong>데이터셋</strong>
                <small>{selected.length}개 연결됨</small>
              </span>
              <span className={styles.menuChevron} aria-hidden="true">
                ›
              </span>
            </button>
          </li>
          <li>
            <button type="button" onClick={() => setPanel("period")}>
              <span className={styles.menuIcon} aria-hidden="true">
                ◷
              </span>
              <span>
                <strong>기간</strong>
                <small>{period.label}</small>
              </span>
              <span className={styles.menuChevron} aria-hidden="true">
                ›
              </span>
            </button>
          </li>
        </ul>
      ) : null}

      {panel === "dataset" ? (
        <div className={styles.menuPanel}>
          <button type="button" className={styles.menuBack} onClick={() => setPanel("root")}>
            ‹ 데이터셋
          </button>
          <p className={styles.menuHint}>연결된 데이터는 모두 켜져 있어요. 빼고 싶은 것만 끄면 돼요.</p>
          <ul className={styles.sourceList}>
            {sources.map((source) => {
              const required = source.id === REQUIRED_SOURCE;
              const on = selected.includes(source.id);
              return (
                <li key={source.id}>
                  <label className={styles.source} data-on={on} data-required={required}>
                    <input
                      type="checkbox"
                      checked={on}
                      disabled={required}
                      onChange={() => onToggleSource(source.id)}
                    />
                    <span className={styles.sourceBox} aria-hidden="true" />
                    <span className={styles.sourceBody}>
                      <strong>
                        {source.label}
                        {required ? <em className={styles.sourceReq}>필수</em> : null}
                      </strong>
                      <small>{source.note}</small>
                      <span className={styles.sourceTopics}>
                        {source.topics.map((topic) => (
                          <i key={topic}>{topic}</i>
                        ))}
                      </span>
                      <span className={styles.sourceInterval}>{source.interval}</span>
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}

      {panel === "period" ? (
        <div className={styles.menuPanel}>
          <button type="button" className={styles.menuBack} onClick={() => setPanel("root")}>
            ‹ 기간
          </button>
          <p className={styles.menuHint}>데이터가 쌓인 구간 안에서 고를 수 있어요.</p>
          <div className={styles.chips}>
            {PERIOD_CHOICES.map((choice) => (
              <button
                key={choice.key}
                type="button"
                className={styles.chip}
                data-on={choice.key === period.key}
                onClick={() => {
                  setCustom(false);
                  onSelectPeriod(choice);
                  onClose();
                }}
              >
                {choice.label}
              </button>
            ))}
            <button
              type="button"
              className={styles.chip}
              data-on={custom || period.key === "custom"}
              onClick={() => setCustom(true)}
            >
              직접 선택
            </button>
          </div>

          {custom || period.key === "custom" ? (
            <div className={styles.range}>
              <label>
                <span>시작일</span>
                <input
                  type="date"
                  value={period.startAt}
                  min={DATA_RANGE.min}
                  max={period.endAt}
                  onChange={(event) => applyCustom(event.target.value, period.endAt)}
                />
              </label>
              <label>
                <span>종료일</span>
                <input
                  type="date"
                  value={period.endAt}
                  min={period.startAt}
                  max={DATA_RANGE.max}
                  onChange={(event) => applyCustom(period.startAt, event.target.value)}
                />
              </label>
            </div>
          ) : (
            <p className={styles.menuRange}>
              {period.startAt} → {period.endAt}
            </p>
          )}
        </div>
      ) : null}
    </div>
  );
}
