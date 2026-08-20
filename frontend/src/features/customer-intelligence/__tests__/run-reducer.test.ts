import { describe, expect, it } from "vitest";

import type {
  AnalysisFact,
  AnalysisGoal,
  AnalysisNote,
  AnalysisPlan,
  AnyRunStreamEvent,
  CustomerSignalReport,
  InsightReport,
  RunArtifact,
  RunRequest,
  RunStreamEvent,
} from "../contracts";
import { initialRunState, runReducer } from "../run-reducer";
import {
  genericArtifact,
  genericFact,
  genericGoal,
  genericNote,
  genericPlan,
  genericRevisedPlan,
  genericReport,
  genericRequest,
} from "./generic-fixtures";

const completedReport: InsightReport = {
  analysis_type: "journey",
  scope: {
    start_at: "2026-07-20T00:00:00+09:00",
    end_at: "2026-08-19T00:00:00+09:00",
    enabled_sources: ["search_history", "search_feedback", "voc"],
    population_description: "검색 실패 후 문의 Journey",
  },
  headline: "검색 실패 후 문의로 이어진 고객 6명",
  executive_summary: "완전한 Journey 패턴이 확인됐습니다.",
  metrics: [
    {
      label: "완전한 Journey 패턴 고객 수",
      value: 6,
      unit: "명",
      result_id: "match_journey_pattern:result",
    },
  ],
  findings: [],
  signal_contributions: [],
  ranked_customers: [
    {
      customer_id: "CUST-003",
      risk_score: 100,
      risk_level: "high",
      signals: [],
      evidence_ids: ["EVD-003"],
      last_event_at: "2026-08-01T10:00:00+09:00",
    },
  ],
  representative_journeys: [],
  representative_journey_ids: [],
  recommendations: [],
  sources_used: ["search_history", "search_feedback", "voc"],
  limitations: [],
};

function event<T extends RunStreamEvent>(value: T): T {
  return value;
}

describe("runReducer", () => {
  it("starts each run from an isolated clean state", () => {
    const dirty = {
      ...initialRunState,
      runId: "run-old",
      phase: "failed" as const,
      error: { code: "old", message: "old error" },
      lastEventId: 8,
    };

    const next = runReducer(dirty, { kind: "start", runId: "run-new" });

    expect(next).toEqual({
      ...initialRunState,
      runId: "run-new",
      phase: "running",
    });
  });

  it("ignores late events from the previous run", () => {
    const state = { ...initialRunState, runId: "run-new", phase: "running" as const };
    const next = runReducer(state, {
      kind: "event",
      runId: "run-old",
      event: event({
        id: 9,
        type: "result",
        data: { agent_mode: "fixture", report: completedReport },
      }),
    });

    expect(next).toBe(state);
  });

  it("deduplicates replayed events and rejects stale IDs", () => {
    const started = runReducer(initialRunState, { kind: "start", runId: "run-1" });
    const current = runReducer(started, {
      kind: "event",
      runId: "run-1",
      event: event({ id: 2, type: "plan", data: { steps: ["분석"] } }),
    });

    const duplicate = runReducer(current, {
      kind: "event",
      runId: "run-1",
      event: event({ id: 2, type: "plan", data: { steps: ["중복"] } }),
    });
    const stale = runReducer(current, {
      kind: "event",
      runId: "run-1",
      event: event({
        id: 1,
        type: "tool_started",
        data: { tool: "catalog_sources", source: [] },
      }),
    });

    expect(duplicate).toBe(current);
    expect(stale).toBe(current);
  });

  it("accumulates trace, validates, stores the result, and selects the first customer", () => {
    let state = runReducer(initialRunState, { kind: "start", runId: "run-1" });
    state = runReducer(state, {
      kind: "event",
      runId: "run-1",
      event: event({ id: 1, type: "plan", data: { steps: ["분석"] } }),
    });
    state = runReducer(state, {
      kind: "event",
      runId: "run-1",
      event: event({
        id: 2,
        type: "validating",
        data: { result_ids: ["match_journey_pattern:result"] },
      }),
    });
    expect(state.phase).toBe("validating");
    state = runReducer(state, {
      kind: "event",
      runId: "run-1",
      event: event({
        id: 3,
        type: "result",
        data: { agent_mode: "fixture", report: completedReport },
      }),
    });
    state = runReducer(state, {
      kind: "event",
      runId: "run-1",
      event: event({ id: 4, type: "done", data: { status: "completed" } }),
    });

    expect(state.phase).toBe("completed");
    expect(state.report).toEqual(completedReport);
    expect(state.agentMode).toBe("fixture");
    expect(state.selectedCustomerId).toBe("CUST-003");
    expect(state.trace.map((item) => item.type)).toEqual([
      "plan",
      "validating",
      "result",
      "done",
    ]);
    expect(state.lastEventId).toBe(4);
  });

  it("finishes in degraded state after an explicit fallback", () => {
    let state = runReducer(initialRunState, { kind: "start", runId: "run-1" });
    state = runReducer(state, {
      kind: "event",
      runId: "run-1",
      event: event({
        id: 1,
        type: "fallback",
        data: { reason: "Gemini timeout", from: "gemini", to: "fixture" },
      }),
    });
    state = runReducer(state, {
      kind: "event",
      runId: "run-1",
      event: event({
        id: 2,
        type: "result",
        data: { agent_mode: "fixture", report: completedReport },
      }),
    });
    state = runReducer(state, {
      kind: "event",
      runId: "run-1",
      event: event({ id: 3, type: "done", data: { status: "completed" } }),
    });

    expect(state.phase).toBe("degraded");
    expect(state.fallbackReason).toBe("Gemini timeout");
    expect(state.agentMode).toBe("fixture");
  });

  it("keeps the public error when a failed run ends", () => {
    let state = runReducer(initialRunState, { kind: "start", runId: "run-1" });
    state = runReducer(state, {
      kind: "event",
      runId: "run-1",
      event: event({
        id: 1,
        type: "error",
        data: { code: "unsupported_question", message: "지원하지 않는 질문입니다." },
      }),
    });
    state = runReducer(state, {
      kind: "event",
      runId: "run-1",
      event: event({ id: 2, type: "done", data: { status: "failed" } }),
    });

    expect(state.phase).toBe("failed");
    expect(state.error).toEqual({
      code: "unsupported_question",
      message: "지원하지 않는 질문입니다.",
    });
  });

  it("rejects a completed terminal event without a validated result", () => {
    const started = runReducer(initialRunState, {
      kind: "start",
      runId: "run-1",
    });

    const state = runReducer(started, {
      kind: "event",
      runId: "run-1",
      event: event({ id: 1, type: "done", data: { status: "completed" } }),
    });

    expect(state.phase).toBe("failed");
    expect(state.report).toBeNull();
    expect(state.agentMode).toBeNull();
    expect(state.error).toEqual({
      code: "protocol_error",
      message: "검증된 분석 결과 없이 Run이 완료되어 결과 표시를 중단했습니다.",
    });
  });

  it("allows customer selection only for the active report", () => {
    let state = runReducer(initialRunState, { kind: "start", runId: "run-1" });
    state = runReducer(state, {
      kind: "event",
      runId: "run-1",
      event: event({
        id: 1,
        type: "result",
        data: {
          agent_mode: "fixture",
          report: {
            ...completedReport,
            ranked_customers: [
              completedReport.ranked_customers[0],
              {
                ...completedReport.ranked_customers[0],
                customer_id: "CUST-007",
              },
            ],
          },
        },
      }),
    });

    const selected = runReducer(state, {
      kind: "select_customer",
      customerId: "CUST-007",
    });
    const unknown = runReducer(selected, {
      kind: "select_customer",
      customerId: "CUST-999",
    });

    expect(selected.selectedCustomerId).toBe("CUST-007");
    expect(unknown).toBe(selected);
  });

  it("retains the full generic goal, plan, Fact, Note and report lifecycle", () => {
    let state = runReducer(initialRunState, {
      kind: "start",
      runId: "run-generic",
      request: genericRequest as RunRequest,
    });
    const events: AnyRunStreamEvent[] = [
      {
        id: 1,
        type: "goal_created",
        data: { goal: genericGoal as AnalysisGoal },
      },
      {
        id: 2,
        type: "plan_created",
        data: { plan: genericPlan as AnalysisPlan },
      },
      {
        id: 3,
        type: "step_started",
        data: {
          step_id: "step-aggregate",
          primitive: "aggregate_events",
          started_at: "2026-08-20T01:00:00Z",
        },
      },
      {
        id: 4,
        type: "fact_created",
        data: {
          step_id: "step-aggregate",
          fact: genericFact as AnalysisFact,
        },
      },
      {
        id: 5,
        type: "analysis_note_created",
        data: { note: genericNote as AnalysisNote },
      },
      {
        id: 6,
        type: "step_completed",
        data: {
          step_id: "step-aggregate",
          status: "completed",
          result_ids: [genericFact.result_id],
          duration_ms: 1000,
        },
      },
      {
        id: 7,
        type: "report_validating",
        data: { fact_ids: [genericFact.fact_id], result_ids: [] },
      },
      {
        id: 8,
        type: "result",
        data: { report: genericReport as CustomerSignalReport },
      },
      { id: 9, type: "done", data: { status: "completed" } },
    ];

    for (const streamEvent of events) {
      state = runReducer(state, {
        kind: "event",
        runId: "run-generic",
        event: streamEvent,
      });
    }

    expect(state.phase).toBe("completed");
    expect(state.status).toBe("completed");
    expect(state.request).toEqual(genericRequest);
    expect(state.goal?.goal_id).toBe(genericGoal.goal_id);
    expect(state.plan?.plan_id).toBe(genericPlan.plan_id);
    expect(state.planHistory).toEqual([genericPlan]);
    expect(state.stepStates["step-aggregate"]).toMatchObject({
      status: "completed",
      primitive: "aggregate_events",
      resultIds: [genericFact.result_id],
    });
    expect(state.facts).toEqual([genericFact]);
    expect(state.notes).toEqual([genericNote]);
    expect(state.runReport).toEqual(genericReport);
    expect(state.report).toBeNull();
    expect(state.limitations).toEqual(genericNote.limitations);
    expect(state.lastEventId).toBe(9);
  });

  it("keeps unique created and revised plans in revision order", () => {
    let state = runReducer(initialRunState, {
      kind: "start",
      runId: "run-plan-history",
    });

    for (const streamEvent of [
      {
        id: 1,
        type: "plan_created" as const,
        data: { plan: genericPlan as AnalysisPlan },
      },
      {
        id: 2,
        type: "plan_revised" as const,
        data: { plan: genericRevisedPlan as AnalysisPlan },
      },
      {
        id: 3,
        type: "plan_revised" as const,
        data: { plan: genericRevisedPlan as AnalysisPlan },
      },
    ]) {
      state = runReducer(state, {
        kind: "event",
        runId: "run-plan-history",
        event: streamEvent,
      });
    }

    expect(state.plan).toEqual(genericRevisedPlan);
    expect(state.planHistory.map((plan) => plan.revision)).toEqual([0, 1]);
  });

  it("pauses for clarification and resumes the same run with the public answer", () => {
    let state = runReducer(initialRunState, {
      kind: "start",
      runId: "run-clarification",
      request: genericRequest as RunRequest,
    });
    state = runReducer(state, {
      kind: "event",
      runId: "run-clarification",
      event: {
        id: 1,
        type: "clarification_required",
        data: {
          kind: "clarification",
          clarification_id: "clarification-1",
          question: "Topic별로 비교할까요?",
        },
      },
    });

    expect(state.phase).toBe("awaiting_clarification");
    expect(state.status).toBe("awaiting_clarification");

    state = runReducer(state, {
      kind: "clarification_submitted",
      runId: "run-clarification",
      clarificationId: "clarification-1",
      answer: "Topic별로 비교해 줘",
    });

    expect(state.runId).toBe("run-clarification");
    expect(state.phase).toBe("running");
    expect(state.status).toBe("running");
    expect(state.clarification).toMatchObject({
      clarification_id: "clarification-1",
      answer: "Topic별로 비교해 줘",
    });
  });

  it("allows an explicit degraded terminal state without a report", () => {
    const started = runReducer(initialRunState, {
      kind: "start",
      runId: "run-degraded",
      request: genericRequest as RunRequest,
    });
    const state = runReducer(started, {
      kind: "event",
      runId: "run-degraded",
      event: {
        id: 1,
        type: "done",
        data: {
          status: "degraded",
          limitations: ["조건에 맞는 공개 데이터가 없습니다."],
        },
      },
    });

    expect(state.phase).toBe("degraded");
    expect(state.status).toBe("degraded");
    expect(state.error).toBeNull();
    expect(state.runReport).toBeNull();
    expect(state.limitations).toEqual([
      "조건에 맞는 공개 데이터가 없습니다.",
    ]);
  });

  it("retains partial public work and safe suggestions when a generic run fails", () => {
    let state = runReducer(initialRunState, {
      kind: "start",
      runId: "run-failed",
      request: genericRequest as RunRequest,
    });
    for (const streamEvent of [
      {
        id: 1,
        type: "goal_created" as const,
        data: { goal: genericGoal as AnalysisGoal },
      },
      {
        id: 2,
        type: "fact_created" as const,
        data: {
          step_id: "step-aggregate",
          fact: genericFact as AnalysisFact,
        },
      },
      {
        id: 3,
        type: "analysis_note_created" as const,
        data: { note: genericNote as AnalysisNote },
      },
      {
        id: 4,
        type: "error" as const,
        data: {
          code: "unsupported_question",
          message: "원본 전체 추출은 지원하지 않습니다.",
          step_id: "step-aggregate",
          suggested_questions: ["부정 피드백 Topic을 비교해 줘"],
        },
      },
      { id: 5, type: "done" as const, data: { status: "failed" as const } },
    ] satisfies AnyRunStreamEvent[]) {
      state = runReducer(state, {
        kind: "event",
        runId: "run-failed",
        event: streamEvent,
      });
    }

    expect(state.phase).toBe("failed");
    expect(state.facts).toEqual([genericFact]);
    expect(state.notes).toEqual([genericNote]);
    expect(state.limitations).toEqual(genericNote.limitations);
    expect(state.suggestedQuestions).toEqual([
      "부정 피드백 Topic을 비교해 줘",
    ]);
    expect(state.error).toMatchObject({
      code: "unsupported_question",
      step_id: "step-aggregate",
    });
  });

  it("hydrates one typed artifact action for completed history", () => {
    const state = runReducer(initialRunState, {
      kind: "hydrate_artifact",
      artifact: genericArtifact as RunArtifact,
    });

    expect(state.runId).toBe(genericArtifact.run_id);
    expect(state.phase).toBe("completed");
    expect(state.goal).toEqual(genericGoal);
    expect(state.planHistory).toEqual([genericPlan, genericRevisedPlan]);
    expect(state.facts).toEqual([genericFact]);
    expect(state.notes).toEqual([genericNote]);
    expect(state.runReport).toEqual(genericReport);
    expect(state.lastEventId).toBe(9);
  });

  it("hydrates a legacy artifact plan when persisted history is empty", () => {
    const state = runReducer(initialRunState, {
      kind: "hydrate_artifact",
      artifact: {
        ...genericArtifact,
        plan: genericPlan,
        plan_history: [],
      } as RunArtifact,
    });

    expect(state.planHistory).toEqual([genericPlan]);
  });
});
