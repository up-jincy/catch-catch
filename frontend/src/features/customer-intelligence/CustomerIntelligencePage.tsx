"use client";

import { AnalysisWorkspace } from "./AnalysisWorkspace";
import { ChatPanel } from "./ChatPanel";
import { EvidenceDrawer } from "./EvidenceDrawer";
import { LegacyInsightWorkspace } from "./LegacyInsightWorkspace";
import { RunHistory } from "./RunHistory";
import type { RunState } from "./run-reducer";
import {
  type CustomerIntelligenceClient,
  useRunController,
} from "./use-run-controller";

interface CustomerIntelligencePageProps {
  client?: CustomerIntelligenceClient;
}

function isGenericWorkspace(state: RunState, networkError: boolean) {
  if (networkError || state.report) return false;
  if (state.runReport?.report_kind === "customer_signal") return true;
  if (state.error?.code === "unsupported_analysis") return true;
  if (
    state.goal || state.plan || state.facts.length || state.notes.length ||
    state.clarification
  ) return true;
  return state.events.some((event) =>
    event.type === "run_started" ||
    event.type === "goal_created" ||
    event.type === "clarification_required" ||
    event.type === "plan_created" ||
    event.type === "plan_revised" ||
    event.type === "step_started" ||
    event.type === "fact_created" ||
    event.type === "analysis_note_created" ||
    event.type === "step_completed" ||
    event.type === "report_validating",
  ) || state.phase === "idle";
}

export function CustomerIntelligencePage({ client }: CustomerIntelligencePageProps) {
  const controller = useRunController(client);
  const { runState } = controller;
  const genericWorkspace = isGenericWorkspace(
    runState,
    controller.submissionErrorKind === "network",
  );

  return (
    <main className="app-page">
      <header className="hero-header">
        <a className="brand" href="#top" aria-label="Signal Trace 홈">
          <span className="brand-mark" aria-hidden="true">ST</span>
          <span>
            <strong>Signal Trace</strong>
            <small>Customer Signal Intelligence</small>
          </span>
        </a>
        <div className="demo-status">
          <span aria-hidden="true" />
          LOCAL WORKING DEMO
        </div>
      </header>

      <section className="hero-copy" id="top">
        <p>ASK · ANALYZE · VERIFY</p>
        <h1>
          흩어진 고객 신호를
          <br />
          <em>검증 가능한 분석</em>으로 엮습니다.
        </h1>
        <p className="hero-description">
          대화형 질문에서 Goal과 Plan, 공개 Fact, Analysis Note, 최종 문서까지
          한 Run의 흐름으로 확인하세요.
        </p>
      </section>

      <div className="workspace-shell">
        <ChatPanel
          composerProps={{
            question: controller.question,
            startDate: controller.startDate,
            endDate: controller.endDate,
            enabledSources: controller.enabledSources,
            sourceOptions: controller.sourceOptions,
            isCreating: controller.isCreating,
            runPhase: runState.phase,
            submissionError: controller.submissionError,
            onQuestionChange: controller.setQuestion,
            onStartDateChange: controller.setStartDate,
            onEndDateChange: controller.setEndDate,
            onToggleSource: controller.toggleSource,
            onSubmit: () => void controller.run(),
          }}
          state={runState}
          isCreating={controller.isCreating}
          clarificationError={controller.clarificationError}
          onSubmitClarification={(answer) =>
            void controller.submitClarification(answer)
          }
          history={
            <RunHistory
              items={controller.historyItems}
              status={controller.historyStatus}
              selectedRunId={controller.selectedRunId}
              onSelect={(runId) => void controller.selectHistory(runId)}
            />
          }
        />

        {genericWorkspace ? (
          <AnalysisWorkspace
            state={runState}
            document={controller.document}
            documentStatus={controller.documentStatus}
            downloadUrls={controller.downloadUrls}
            onOpenEvidence={controller.openEvidence}
          />
        ) : (
          <LegacyInsightWorkspace controller={controller} />
        )}
      </div>

      <footer className="app-footer">
        <span>Seed 20260819 · Synthetic data</span>
        <span>수치와 근거 연결은 결정론적 Analytics가 계산합니다.</span>
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
