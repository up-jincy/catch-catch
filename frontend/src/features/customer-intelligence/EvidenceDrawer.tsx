"use client";

import { useEffect, useRef } from "react";

import type { EvidenceResult, Scalar, SourceId } from "./contracts";
import type { DetailState } from "./use-run-controller";

interface EvidenceDrawerProps {
  evidenceId: string | null;
  state: DetailState<EvidenceResult>;
  opener: HTMLElement | null;
  onClose: () => void;
  onRetry: () => void;
}

const sourceLabels: Record<SourceId, string> = {
  search_history: "검색 이력",
  search_feedback: "검색 피드백",
  voc: "VOC",
};

const seoulTime = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul",
  year: "numeric",
  month: "long",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});

const focusableSelector = [
  "button:not([disabled])",
  "[href]",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

function rawValue(value: Scalar) {
  if (value === null) return "—";
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

export function EvidenceDrawer({
  evidenceId,
  state,
  opener,
  onClose,
  onRetry,
}: EvidenceDrawerProps) {
  const dialogRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!evidenceId) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab") return;

      const dialog = dialogRef.current;
      if (!dialog) return;
      const focusable = Array.from(
        dialog.querySelectorAll<HTMLElement>(focusableSelector),
      ).filter((element) => !element.hasAttribute("disabled"));
      if (!focusable.length) {
        event.preventDefault();
        dialog.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable.at(-1) ?? first;
      if (focusable.length === 1) {
        event.preventDefault();
        first.focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
      opener?.focus();
    };
  }, [evidenceId, onClose, opener]);

  if (!evidenceId) return null;

  return (
    <div className="drawer-layer">
      <button
        className="drawer-backdrop"
        type="button"
        aria-label="Evidence 닫기"
        tabIndex={-1}
        onClick={onClose}
      />
      <aside
        ref={dialogRef}
        className="evidence-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="evidence-title"
        tabIndex={-1}
      >
        <div className="drawer-header">
          <div>
            <p className="section-kicker">MASKED SOURCE RECORD</p>
            <h2 id="evidence-title">Evidence · {evidenceId}</h2>
          </div>
          <button
            ref={closeRef}
            className="drawer-close"
            type="button"
            aria-label="Evidence 닫기"
            onClick={onClose}
          >
            ×
          </button>
        </div>

        <div className="drawer-body">
          {state.status === "loading" || state.status === "idle" ? (
            <div className="detail-loading" aria-busy="true">
              <span className="loading-ring" aria-hidden="true" />
              <p>Run에서 허용된 마스킹 원본을 확인하고 있어요.</p>
            </div>
          ) : state.status === "error" ? (
            <div className="detail-error" role="alert">
              <strong>Evidence를 표시하지 못했습니다.</strong>
              <p>{state.error}</p>
              <button className="secondary-action" type="button" onClick={onRetry}>
                Evidence 다시 불러오기
              </button>
            </div>
          ) : state.status === "empty" || state.data.records.length === 0 ? (
            <div className="panel-placeholder zero-placeholder">
              <span aria-hidden="true">0</span>
              <p>표시할 근거가 없습니다.</p>
            </div>
          ) : (
            state.data.records.map((record) => (
              <article className="evidence-record" key={record.evidence_id}>
                <div className="record-summary">
                  <span className={`record-source source-${record.source_id}`}>
                    {sourceLabels[record.source_id]}
                  </span>
                  <time dateTime={record.occurred_at}>
                    {seoulTime.format(new Date(record.occurred_at))}
                  </time>
                  <h3>{record.summary}</h3>
                  <p>
                    마스킹 고객 <strong>{record.masked_customer_id}</strong>
                  </p>
                </div>
                <div className="raw-record">
                  <div className="raw-title">
                    <h3>원본 필드</h3>
                    <span>민감정보 마스킹 적용</span>
                  </div>
                  <dl>
                    {Object.entries(record.raw_fields)
                      .sort(([left], [right]) => left.localeCompare(right))
                      .map(([key, value]) => (
                        <div key={key}>
                          <dt>{key}</dt>
                          <dd>{rawValue(value)}</dd>
                        </div>
                      ))}
                  </dl>
                </div>
              </article>
            ))
          )}
        </div>

        <div className="drawer-footer">
          이 화면은 현재 Run이 참조한 Evidence만 조회합니다.
        </div>
      </aside>
    </div>
  );
}
