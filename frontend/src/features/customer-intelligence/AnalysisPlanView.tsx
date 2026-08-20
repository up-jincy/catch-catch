import type { AnalysisFact, AnalysisPlan } from "./contracts";
import type { StepExecutionState } from "./run-reducer";
import { FactDetail } from "./FactDetail";

interface AnalysisPlanViewProps {
  plan: AnalysisPlan;
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
  stepStates,
  facts,
  onOpenEvidence,
}: AnalysisPlanViewProps) {
  return (
    <section className="panel analysis-card plan-card" aria-labelledby="plan-title">
      <div className="workspace-heading">
        <div>
          <p className="section-kicker">02 · PLAN</p>
          <h2 id="plan-title">실행 계획</h2>
        </div>
        <span className="revision-chip">revision {plan.revision}</span>
      </div>
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
