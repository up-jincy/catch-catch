import type { ArtifactDocument, GenericOrLegacyReport } from "./contracts";

interface RunDocumentViewProps {
  document: ArtifactDocument | null;
  report: GenericOrLegacyReport | null;
  status: "idle" | "loading" | "success" | "error";
}

export function RunDocumentView({ document, report, status }: RunDocumentViewProps) {
  const resolvedReport = document?.report ?? report;
  if (status === "loading") {
    return <section className="panel analysis-card document-card" aria-busy="true">문서 기록을 불러오는 중입니다.</section>;
  }
  if (!resolvedReport && !document) return null;
  return (
    <section className="panel analysis-card document-card" aria-labelledby="document-title">
      <p className="section-kicker">04 · RUN DOCUMENT</p>
      <h2 id="document-title">{resolvedReport?.headline ?? document?.headline}</h2>
      {resolvedReport ? <p className="document-summary">{resolvedReport.executive_summary}</p> : null}
      {(document?.limitations ?? resolvedReport?.limitations ?? []).length ? (
        <div className="document-limitations">
          <h3>한계</h3>
          <ul>
            {(document?.limitations ?? resolvedReport?.limitations ?? []).map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
