import type { AnalysisFact, AnalysisPlan } from "./contracts";
import type { StepExecutionState } from "./run-reducer";
import { FactDetail } from "./FactDetail";

interface AnalysisPlanViewProps {
  plan: AnalysisPlan;
  planHistory: AnalysisPlan[];
  stepStates: Record<string, StepExecutionState>;
  facts: AnalysisFact[];
  onOpenEvidence: (evidenceId: string, opener: HTMLElement) => void;
}

const statusLabels = {
  pending: "대기",
  running: "실행 중",
  completed: "완료",
  degraded: "제한 완료",
  failed: "실패",
};

export function AnalysisPlanView({
  plan,
  planHistory,
  stepStates,
  facts,
  onOpenEvidence,
}: AnalysisPlanViewProps) {
  const revisions = (planHistory.length ? planHistory : [plan])
    .map((recorded) => recorded.revision)
    .join(" → ");
  return (
    <section className="panel analysis-card plan-card" aria-labelledby="plan-title">
      <div className="workspace-heading">
        <div>
          <p className="section-kicker">02 · PLAN</p>
          <h2 id="plan-title">실행 계획</h2>
        </div>
        <span className="revision-chip">revision {revisions}</span>
      </div>
      <p className="document-summary">
        <strong>계획 근거</strong> <span>{plan.rationale}</span>
      </p>
      <ol className="analysis-plan-list">
        {plan.steps.map((step, index) => {
          const execution = stepStates[step.step_id];
          const stepFacts = facts.filter((fact) => fact.step_id === step.step_id);
          return (
            <li className={`analysis-step step-${execution?.status ?? "pending"}`} key={step.step_id}>
              <div className="step-heading">
                <span>{String(index + 1).padStart(2, "0")}</span>
                <div>
                  <h3>{step.primitive}</h3>
                  <p>{step.source_ids.join(" · ")}</p>
                </div>
                <strong>{statusLabels[execution?.status ?? "pending"]}</strong>
              </div>
              <div className="fact-body">
                <p className="processing-stats">
                  <strong>선택 근거</strong> <span>{step.selection_reason}</span>
                </p>
                <dl className="fact-refs">
                  <div>
                    <dt>Source IDs</dt>
                    <dd>{step.source_ids.join(" · ")}</dd>
                  </div>
                  <div>
                    <dt>Parameters</dt>
                    <dd><code>{JSON.stringify(step.parameters)}</code></dd>
                  </div>
                </dl>
              </div>
              {stepFacts.map((fact) => (
                <FactDetail
                  key={fact.fact_id}
                  fact={fact}
                  onOpenEvidence={onOpenEvidence}
                />
              ))}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
