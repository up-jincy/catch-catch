import type { InsightReport, RunPhase } from "./contracts";
import { InsightSummary } from "./InsightSummary";
import { JourneyTimeline } from "./JourneyTimeline";
import { RankedCustomers } from "./RankedCustomers";
import type { ReturnTypeUseRunController } from "./use-run-controller";

interface LegacyInsightWorkspaceProps {
  controller: ReturnTypeUseRunController;
}

function isTerminal(phase: RunPhase) {
  return phase === "completed" || phase === "degraded";
}

export function LegacyInsightWorkspace({
  controller,
}: LegacyInsightWorkspaceProps) {
  const { runState } = controller;
  const terminal = isTerminal(runState.phase);
  const terminalReport: InsightReport | null = terminal ? runState.report : null;
  const terminalCustomerId = terminal ? runState.selectedCustomerId : null;

  return (
    <section className="result-workspace legacy-insight-workspace" aria-label="기존 Journey Insight">
      <InsightSummary
        report={runState.report}
        phase={runState.phase}
        agentMode={runState.agentMode}
        fallbackReason={runState.fallbackReason}
        submissionError={controller.submissionError}
        submissionErrorKind={controller.submissionErrorKind}
        submissionErrorCode={controller.submissionErrorCode}
        isCreating={controller.isCreating}
        error={runState.error}
        onRetry={() => void controller.run()}
      />
      <div className="detail-grid">
        <RankedCustomers
          customers={terminalReport?.ranked_customers ?? []}
          selectedCustomerId={terminalCustomerId}
          hasReport={Boolean(terminalReport)}
          onSelectCustomer={controller.selectCustomer}
        />
        <JourneyTimeline
          customerId={terminalCustomerId}
          state={controller.journeyState}
          onOpenEvidence={controller.openEvidence}
          onRetry={controller.retryJourney}
        />
      </div>
    </section>
  );
}
