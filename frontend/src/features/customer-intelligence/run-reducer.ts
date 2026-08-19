import type {
  AgentMode,
  InsightReport,
  RunError,
  RunPhase,
  RunStreamEvent,
} from "./contracts";

export interface RunState {
  runId: string | null;
  phase: RunPhase;
  agentMode: AgentMode | null;
  report: InsightReport | null;
  selectedCustomerId: string | null;
  trace: RunStreamEvent[];
  error: RunError | null;
  fallbackReason: string | null;
  lastEventId: number;
}

export const initialRunState: RunState = {
  runId: null,
  phase: "idle",
  agentMode: null,
  report: null,
  selectedCustomerId: null,
  trace: [],
  error: null,
  fallbackReason: null,
  lastEventId: 0,
};

export type RunAction =
  | { kind: "start"; runId: string }
  | { kind: "event"; runId: string; event: RunStreamEvent }
  | { kind: "failed"; runId: string; error: RunError }
  | { kind: "select_customer"; customerId: string }
  | { kind: "reset" };

function startState(runId: string): RunState {
  return { ...initialRunState, runId, phase: "running" };
}

function fallbackReason(event: Extract<RunStreamEvent, { type: "fallback" }>): string {
  return (
    event.data.reason ??
    event.data.message ??
    "Gemini 분석에서 Fixture Replay로 전환했습니다."
  );
}

function reduceEvent(state: RunState, event: RunStreamEvent): RunState {
  const next: RunState = {
    ...state,
    trace: [...state.trace, event],
    lastEventId: event.id,
  };

  switch (event.type) {
    case "validating":
      return { ...next, phase: "validating" };
    case "result":
      return {
        ...next,
        agentMode: event.data.agent_mode,
        report: event.data.report,
        selectedCustomerId:
          event.data.report.ranked_customers[0]?.customer_id ?? null,
      };
    case "fallback":
      return {
        ...next,
        agentMode: "fixture",
        fallbackReason: fallbackReason(event),
      };
    case "error":
      return { ...next, phase: "failed", error: event.data };
    case "done":
      if (event.data.status === "failed" || next.error) {
        return { ...next, phase: "failed" };
      }
      if (!next.report || !next.agentMode) {
        return {
          ...next,
          phase: "failed",
          error: {
            code: "protocol_error",
            message:
              "검증된 분석 결과 없이 Run이 완료되어 결과 표시를 중단했습니다.",
          },
        };
      }
      return {
        ...next,
        phase: next.fallbackReason ? "degraded" : "completed",
      };
    default:
      return next;
  }
}

export function runReducer(state: RunState, action: RunAction): RunState {
  switch (action.kind) {
    case "start":
      return startState(action.runId);
    case "reset":
      return { ...initialRunState };
    case "failed":
      if (state.runId !== action.runId) {
        return state;
      }
      return { ...state, phase: "failed", error: action.error };
    case "select_customer":
      if (
        !state.report?.ranked_customers.some(
          (customer) => customer.customer_id === action.customerId,
        )
      ) {
        return state;
      }
      return { ...state, selectedCustomerId: action.customerId };
    case "event":
      if (
        state.runId !== action.runId ||
        action.event.id <= state.lastEventId
      ) {
        return state;
      }
      return reduceEvent(state, action.event);
  }
}
