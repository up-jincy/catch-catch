import type { AnalysisFact } from "./contracts";
import { sourceLabel } from "./source-catalog";

interface FactDetailProps {
  fact: AnalysisFact;
  onOpenEvidence: (evidenceId: string, opener: HTMLElement) => void;
}

function metricValue(value: number, unit: string): string {
  return `${value.toLocaleString("ko-KR")}${unit}`;
}

export function FactDetail({ fact, onOpenEvidence }: FactDetailProps) {
  const processing = fact.payload.processing;
  return (
    <details className="fact-detail" open>
      <summary>
        <span>검증 Fact</span>
        <strong>{fact.primitive}</strong>
      </summary>
      <div className="fact-body">
        <div className="fact-metrics">
          {fact.metrics.map((metric) => (
            <div key={`${fact.fact_id}-${metric.metric_key}`}>
              <span>{metric.label}</span>
              <strong>{metricValue(metric.value, metric.unit)}</strong>
            </div>
          ))}
        </div>
        <p className="processing-stats">
          스캔 {processing.scanned_events.toLocaleString("ko-KR")} · 매칭 {processing.matched_events.toLocaleString("ko-KR")} · 반환 {processing.returned_rows.toLocaleString("ko-KR")}
        </p>
        <dl className="fact-refs">
          <div><dt>Source</dt><dd>{fact.source_ids.map(sourceLabel).join(" · ")}</dd></div>
          <div><dt>result_id</dt><dd><code>{fact.result_id}</code></dd></div>
        </dl>
        {fact.evidence_ids.length ? (
          <div className="fact-evidence" aria-label="Fact Evidence">
            {fact.evidence_ids.map((evidenceId) => (
              <button
                className="evidence-action"
                type="button"
                key={evidenceId}
                onClick={(event) => onOpenEvidence(evidenceId, event.currentTarget)}
              >
                {evidenceId}
              </button>
            ))}
          </div>
        ) : null}
      </div>
    </details>
  );
}
