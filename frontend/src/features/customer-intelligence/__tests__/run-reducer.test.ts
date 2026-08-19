import { describe, expect, it } from "vitest";

import type { InsightReport, RunStreamEvent } from "../contracts";
import { initialRunState, runReducer } from "../run-reducer";

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
});
