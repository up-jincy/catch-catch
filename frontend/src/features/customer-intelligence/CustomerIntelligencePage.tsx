"use client";

import { AgentTrace } from "./AgentTrace";
import { EvidenceDrawer } from "./EvidenceDrawer";
import { InsightSummary } from "./InsightSummary";
import { JourneyTimeline } from "./JourneyTimeline";
import { QueryComposer } from "./QueryComposer";
import { RankedCustomers } from "./RankedCustomers";
import {
  type CustomerIntelligenceClient,
  useRunController,
} from "./use-run-controller";

interface CustomerIntelligencePageProps {
  client?: CustomerIntelligenceClient;
}

export function CustomerIntelligencePage({ client }: CustomerIntelligencePageProps) {
  const controller = useRunController(client);
  const { runState } = controller;
  const isTerminal =
    runState.phase === "completed" || runState.phase === "degraded";
  const terminalReport = isTerminal ? runState.report : null;
  const terminalCustomerId = isTerminal ? runState.selectedCustomerId : null;
  const hasReport = Boolean(terminalReport);

  return (
    <main className="app-page">
      <header className="hero-header">
        <a className="brand" href="#top" aria-label="Signal Trace 홈">
          <span className="brand-mark" aria-hidden="true">
            ST
          </span>
          <span>
            <strong>Signal Trace</strong>
            <small>Customer Journey Intelligence</small>
          </span>
        </a>
        <div className="demo-status">
          <span aria-hidden="true" />
          LOCAL WORKING DEMO
        </div>
      </header>

      <section className="hero-copy" id="top">
        <p>ASK · TRACE · VERIFY</p>
        <h1>
          고객의 막힌 순간을
          <br />
          <em>근거 있는 Journey</em>로 찾습니다.
        </h1>
        <p className="hero-description">
          자연어 질문 하나로 검색과 피드백, 상담 기록을 연결하고 분석 과정부터
          원본 Evidence까지 같은 화면에서 확인하세요.
        </p>
      </section>

      <div className="workspace-shell">
        <aside className="control-rail" aria-label="질문과 Agent 진행 상황">
          <QueryComposer
            question={controller.question}
            startDate={controller.startDate}
            endDate={controller.endDate}
            enabledSources={controller.enabledSources}
            isCreating={controller.isCreating}
            runPhase={runState.phase}
            submissionError={controller.submissionError}
            onQuestionChange={controller.setQuestion}
            onStartDateChange={controller.setStartDate}
            onEndDateChange={controller.setEndDate}
            onToggleSource={controller.toggleSource}
            onSubmit={() => void controller.run()}
          />
          <AgentTrace
            events={runState.trace}
            phase={runState.phase}
            fallbackReason={runState.fallbackReason}
            isCreating={controller.isCreating}
          />
        </aside>

        <div className="result-workspace">
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
              hasReport={hasReport}
              onSelectCustomer={controller.selectCustomer}
            />
            <JourneyTimeline
              customerId={terminalCustomerId}
              state={controller.journeyState}
              onOpenEvidence={controller.openEvidence}
              onRetry={controller.retryJourney}
            />
          </div>
        </div>
      </div>

      <footer className="app-footer">
        <span>Seed 20260819 · Synthetic data</span>
        <span>수치와 고객 매칭은 결정론적 Analytics가 계산합니다.</span>
      </footer>

      <EvidenceDrawer
        evidenceId={controller.evidenceId}
        state={controller.evidenceState}
        opener={controller.evidenceOpener}
        onClose={controller.closeEvidence}
        onRetry={controller.retryEvidence}
      />
    </main>
  );
}
