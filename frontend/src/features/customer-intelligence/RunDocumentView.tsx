import type { ArtifactDocument, GenericOrLegacyReport } from "./contracts";

interface RunDocumentViewProps {
  document: ArtifactDocument | null;
  report: GenericOrLegacyReport | null;
  status: "idle" | "loading" | "success" | "error";
}

interface FindingView {
  key: string;
  statement: string;
  supportSummary: string | null;
}

interface RecommendationView {
  key: string;
  title: string;
  reason: string;
}

interface MetricView {
  key: string;
  label: string;
  value: number | string;
  unit: string | null;
}

function findingViews(report: GenericOrLegacyReport): FindingView[] {
  if (report.report_kind === "customer_signal") {
    return report.findings.map((finding) => ({
      key: finding.claim.claim_id,
      statement: finding.statement,
      supportSummary: `근거 Fact ${finding.fact_ids.length}개 · Evidence ${finding.evidence_ids.length}개`,
    }));
  }
  return report.findings.map((finding, index) => ({
    key: `${index}-${finding.title}`,
    statement: `${finding.title} — ${finding.description}`,
    supportSummary: finding.evidence_ids.length
      ? `Evidence ${finding.evidence_ids.length}개`
      : null,
  }));
}

function recommendationViews(report: GenericOrLegacyReport): RecommendationView[] {
  return report.recommendations.map((recommendation) => ({
    key: recommendation.action_id,
    title: recommendation.title,
    reason: recommendation.reason,
  }));
}

function metricViews(report: GenericOrLegacyReport): MetricView[] {
  return report.metrics.map((metric, index) => ({
    key: `${index}-${metric.label}`,
    label: metric.label,
    value: metric.value,
    unit: metric.unit,
  }));
}

export function RunDocumentView({ document, report, status }: RunDocumentViewProps) {
  const resolvedReport = document?.report ?? report;
  if (status === "loading") {
    return <section className="panel analysis-card document-card" aria-busy="true">문서 기록을 불러오는 중입니다.</section>;
  }
  if (!resolvedReport && !document) return null;
  const findings = resolvedReport ? findingViews(resolvedReport) : [];
  const metrics = resolvedReport ? metricViews(resolvedReport) : [];
  const recommendations = resolvedReport ? recommendationViews(resolvedReport) : [];
  const limitations = document?.limitations ?? resolvedReport?.limitations ?? [];
  return (
    <section className="panel analysis-card document-card" aria-labelledby="document-title">
      <p className="section-kicker">04 · 분석 결론</p>
      <h2 id="document-title">{resolvedReport?.headline ?? document?.headline}</h2>
      {resolvedReport ? <p className="document-summary">{resolvedReport.executive_summary}</p> : null}

      {findings.length ? (
        <div className="document-findings">
          <h3>무엇을 알게 됐나 — 검증된 발견</h3>
          <ul>
            {findings.map((finding) => (
              <li key={finding.key}>
                <p>{finding.statement}</p>
                {finding.supportSummary ? <span>{finding.supportSummary}</span> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {metrics.length ? (
        <div className="document-metrics">
          <h3>핵심 지표</h3>
          <ul>
            {metrics.map((metric) => (
              <li key={metric.key}>
                <span>{metric.label}</span>
                <b>
                  {metric.value}
                  {metric.unit ? ` ${metric.unit}` : ""}
                </b>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {recommendations.length ? (
        <div className="document-recommendations">
          <h3>무엇을 해야 하나 — 권장 액션</h3>
          <ol className="recommendation-list">
            {recommendations.map((recommendation, index) => (
              <li key={recommendation.key}>
                <article>
                  <span>{index + 1}</span>
                  <div>
                    <h4>{recommendation.title}</h4>
                    <p>{recommendation.reason}</p>
                  </div>
                </article>
              </li>
            ))}
          </ol>
        </div>
      ) : null}

      {limitations.length ? (
        <div className="document-limitations">
          <h3>한계</h3>
          <ul>
            {limitations.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
