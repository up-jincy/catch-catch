import type { AnalysisFact } from "./contracts";
import { payloadHighlights, primitiveLabel } from "./primitive-catalog";
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
  const provenance = fact.payload.provenance;
  const highlights = payloadHighlights(fact.payload);
  return (
    <details className="fact-detail" open>
      <summary>
        <span>출력 (Tool Output) · 검증 Fact</span>
        <strong>{primitiveLabel(fact.primitive)}</strong>
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
          이벤트 {processing.scanned_events.toLocaleString("ko-KR")}건 스캔 → {processing.matched_events.toLocaleString("ko-KR")}건 조건 일치 → {processing.returned_rows.toLocaleString("ko-KR")}행 반환
        </p>
        {highlights.length ? (
          <ul className="fact-highlights" aria-label="분석 결과">
            {highlights.map((highlight, index) => (
              <li key={`${index}-${highlight}`}>{highlight}</li>
            ))}
          </ul>
        ) : null}
        <dl className="fact-refs">
          <div><dt>Source</dt><dd>{fact.source_ids.map(sourceLabel).join(" · ")}</dd></div>
          <div><dt>result_id</dt><dd><code>{fact.result_id}</code></dd></div>
        </dl>
        <details className="fact-provenance">
          <summary>Provenance 상세</summary>
          <dl className="fact-refs">
            <div>
              <dt>Interval</dt>
              <dd>{provenance.scope.start_at} → {provenance.scope.end_at}</dd>
            </div>
            <div>
              <dt>Scope</dt>
              <dd>{provenance.scope.source_ids.join(" · ")} · max {provenance.scope.max_events}</dd>
            </div>
            <div>
              <dt>Dataset version</dt>
              <dd><code>{provenance.dataset_version}</code></dd>
            </div>
            <div>
              <dt>Manifest versions</dt>
              <dd><code>{JSON.stringify(provenance.manifest_versions)}</code></dd>
            </div>
            <div>
              <dt>Adapter versions</dt>
              <dd><code>{JSON.stringify(provenance.adapter_versions)}</code></dd>
            </div>
          </dl>
        </details>
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
