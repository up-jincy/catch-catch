import type { AnalysisGoal } from "./contracts";
import { sourceLabel } from "./source-catalog";

interface AnalysisGoalCardProps {
  goal: AnalysisGoal;
}

export function AnalysisGoalCard({ goal }: AnalysisGoalCardProps) {
  return (
    <section className="panel analysis-card goal-card" aria-labelledby="goal-title">
      <p className="section-kicker">01 · GOAL</p>
      <h2 id="goal-title">{goal.objective}</h2>
      <p>{goal.population.description}</p>
      <dl className="goal-grid">
        <div>
          <dt>결과 형태</dt>
          <dd>{goal.output}</dd>
        </div>
        <div>
          <dt>기간</dt>
          <dd>{goal.time_range.start_at.slice(0, 10)} → {goal.time_range.end_at.slice(0, 10)}</dd>
        </div>
        <div>
          <dt>Source</dt>
          <dd>{goal.source_ids.map(sourceLabel).join(" · ")}</dd>
        </div>
      </dl>
    </section>
  );
}
