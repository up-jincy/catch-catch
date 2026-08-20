import type { ArtifactDocument } from "./contracts";
import { AnalysisGoalCard } from "./AnalysisGoalCard";
import { AnalysisNoteTimeline } from "./AnalysisNoteTimeline";
import { AnalysisPlanView } from "./AnalysisPlanView";
import { RunDocumentView } from "./RunDocumentView";
import { RunDownloads } from "./RunDownloads";
import type { RunState } from "./run-reducer";

interface AnalysisWorkspaceProps {
  state: RunState;
  document: ArtifactDocument | null;
  documentStatus: "idle" | "loading" | "success" | "error";
  downloadUrls: { json: string; markdown: string } | null;
  onOpenEvidence: (evidenceId: string, opener: HTMLElement) => void;
}

export function AnalysisWorkspace({
  state,
  document,
  documentStatus,
  downloadUrls,
  onOpenEvidence,
}: AnalysisWorkspaceProps) {
  const hasAnalysis = Boolean(state.goal || state.plan || state.facts.length || state.notes.length);
  return (
    <section className="result-workspace analysis-workspace" aria-label="분석 Workspace">
      {!hasAnalysis && state.phase === "idle" ? (
        <section className="panel analysis-welcome" aria-labelledby="workspace-welcome-title">
          <p className="section-kicker">ANALYSIS WORKSPACE</p>
          <h2 id="workspace-welcome-title">질문에서 검증된 기록까지</h2>
          <p>Goal, 실행 Plan, 공개 Fact와 Analysis Note를 한 흐름으로 확인합니다.</p>
        </section>
      ) : null}

      {state.goal ? <AnalysisGoalCard goal={state.goal} /> : null}
      {state.plan ? (
        <AnalysisPlanView
          plan={state.plan}
          planHistory={state.planHistory}
          stepStates={state.stepStates}
          facts={state.facts}
          onOpenEvidence={onOpenEvidence}
        />
      ) : null}
      <AnalysisNoteTimeline notes={state.notes} />

      {state.phase === "degraded" && !state.runReport ? (
        <section className="panel analysis-card degraded-record" aria-labelledby="degraded-title">
          <p className="section-kicker">DEGRADED · RECORDED</p>
          <h2 id="degraded-title">결론 없이 기록된 Run</h2>
          <ul>
            {state.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}
          </ul>
        </section>
      ) : null}

      {state.phase === "failed" ? (
        <section className="panel analysis-card failed-record" aria-labelledby="failed-title">
          <p className="section-kicker">PARTIAL RUN</p>
          <h2 id="failed-title">부분 기록을 보존했습니다</h2>
          <p>{state.error?.message ?? "분석을 완료하지 못했습니다."}</p>
        </section>
      ) : null}

      <RunDocumentView
        document={document}
        report={state.runReport}
        status={documentStatus}
      />
      <RunDownloads urls={downloadUrls} />
    </section>
  );
}
