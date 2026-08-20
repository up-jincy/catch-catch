import type {
  AgentMode,
  AnalysisFact,
  AnalysisGoal,
  AnalysisNote,
  AnalysisPlan,
  AnyRunStreamEvent,
  ClarificationRecord,
  CustomerSignalReport,
  GenericOrLegacyReport,
  GenericPrimitiveName,
  InsightReport,
  RunArtifact,
  RunError,
  RunPhase,
  RunRequest,
  RunStatus,
  RunStreamEvent,
} from "./contracts";

export type StepExecutionStatus =
  | "pending"
  | "running"
  | "completed"
  | "degraded"
  | "failed";

export interface StepExecutionState {
  stepId: string;
  primitive: GenericPrimitiveName;
  status: StepExecutionStatus;
  startedAt: string | null;
  durationMs: number | null;
  resultIds: string[];
}

export interface RunState {
  runId: string | null;
  request: RunRequest | null;
  status: RunStatus | null;
  phase: RunPhase;
  agentMode: AgentMode | null;
  /** Legacy report surface retained until the existing components migrate. */
  report: InsightReport | null;
  /** Authoritative generic-or-legacy result used by the new workspace. */
  runReport: GenericOrLegacyReport | null;
  goal: AnalysisGoal | null;
  clarification: ClarificationRecord | null;
  plan: AnalysisPlan | null;
  stepStates: Record<string, StepExecutionState>;
  facts: AnalysisFact[];
  notes: AnalysisNote[];
  limitations: string[];
  suggestedQuestions: string[];
  selectedCustomerId: string | null;
  trace: RunStreamEvent[];
  /** Complete generic + legacy event ledger for the analysis workspace. */
  events: AnyRunStreamEvent[];
  error: RunError | null;
  fallbackReason: string | null;
  lastEventId: number;
}

export const initialRunState: RunState = {
  runId: null,
  request: null,
  status: null,
  phase: "idle",
  agentMode: null,
  report: null,
  runReport: null,
  goal: null,
  clarification: null,
  plan: null,
  stepStates: {},
  facts: [],
  notes: [],
  limitations: [],
  suggestedQuestions: [],
  selectedCustomerId: null,
  trace: [],
  events: [],
  error: null,
  fallbackReason: null,
  lastEventId: 0,
};

export type RunAction =
  | { kind: "start"; runId: string; request?: RunRequest }
  | { kind: "event"; runId: string; event: AnyRunStreamEvent }
  | { kind: "failed"; runId: string; error: RunError }
  | {
      kind: "clarification_submitted";
      runId: string;
      clarificationId: string;
      answer: string;
    }
  | { kind: "hydrate_artifact"; artifact: RunArtifact }
  | { kind: "select_customer"; customerId: string }
  | { kind: "reset" };

function isGenericReport(
  report: GenericOrLegacyReport,
): report is CustomerSignalReport {
  return report.report_kind === "customer_signal";
}

function startState(runId: string, request?: RunRequest): RunState {
  return {
    ...initialRunState,
    runId,
    request: request ?? null,
    phase: "running",
  };
}

function fallbackReason(event: Extract<RunStreamEvent, { type: "fallback" }>): string {
  return (
    event.data.reason ??
    event.data.message ??
    "Gemini 분석에서 Fixture Replay로 전환했습니다."
  );
}

function unique(values: Iterable<string>): string[] {
  return [...new Set(values)];
}

function phaseForStatus(status: RunStatus): RunPhase {
  switch (status) {
    case "queued":
    case "running":
      return "running";
    case "awaiting_clarification":
      return "awaiting_clarification";
    case "completed":
      return "completed";
    case "degraded":
      return "degraded";
    case "failed":
      return "failed";
  }
}

function stepStatesForPlan(
  plan: AnalysisPlan,
  current: Record<string, StepExecutionState> = {},
): Record<string, StepExecutionState> {
  const next = { ...current };
  for (const step of plan.steps) {
    const existing = current[step.step_id];
    next[step.step_id] = existing ?? {
      stepId: step.step_id,
      primitive: step.primitive,
      status: "pending",
      startedAt: null,
      durationMs: null,
      resultIds: [],
    };
  }
  return next;
}

function firstCustomerId(report: GenericOrLegacyReport | null): string | null {
  return report?.ranked_customers[0]?.customer_id ?? null;
}

function isLegacyTraceEvent(event: AnyRunStreamEvent): event is RunStreamEvent {
  return (
    event.type === "plan" ||
    event.type === "tool_started" ||
    event.type === "tool_completed" ||
    event.type === "validating" ||
    event.type === "result" ||
    event.type === "error" ||
    event.type === "fallback" ||
    event.type === "done"
  );
}

function nextLegacyTrace(
  state: RunState,
  event: AnyRunStreamEvent,
): RunStreamEvent[] {
  const genericSteps =
    event.type === "step_started" ||
    event.type === "step_completed" ||
    state.events.some(
      (item) => item.type === "step_started" || item.type === "step_completed",
    );
  const genericPlan =
    event.type === "plan_created" ||
    event.type === "plan_revised" ||
    state.events.some(
      (item) => item.type === "plan_created" || item.type === "plan_revised",
    );
  const genericValidation =
    event.type === "report_validating" ||
    state.events.some((item) => item.type === "report_validating");

  const trace = state.trace.filter((item) => {
    if (
      genericSteps &&
      (item.type === "tool_started" || item.type === "tool_completed")
    ) {
      return false;
    }
    if (genericPlan && item.type === "plan") return false;
    if (genericValidation && item.type === "validating") return false;
    return true;
  });

  if (!isLegacyTraceEvent(event)) return trace;
  if (
    genericSteps &&
    (event.type === "tool_started" || event.type === "tool_completed")
  ) {
    return trace;
  }
  if (genericPlan && event.type === "plan") return trace;
  if (genericValidation && event.type === "validating") return trace;
  return [...trace, event];
}

function reduceEvent(state: RunState, event: AnyRunStreamEvent): RunState {
  const next: RunState = {
    ...state,
    trace: nextLegacyTrace(state, event),
    events: [...state.events, event],
    lastEventId: event.id,
  };

  switch (event.type) {
    case "run_started":
      return {
        ...next,
        status: event.data.status,
        phase: phaseForStatus(event.data.status),
      };
    case "goal_created":
      return {
        ...next,
        goal: event.data.goal,
        status: "running",
        phase: "running",
        clarification: null,
      };
    case "clarification_required":
      return {
        ...next,
        status: "awaiting_clarification",
        phase: "awaiting_clarification",
        clarification: {
          ...event.data,
          answer: null,
          requested_at: null,
          answered_at: null,
        },
      };
    case "plan_created":
    case "plan_revised":
      return {
        ...next,
        plan: event.data.plan,
        stepStates: stepStatesForPlan(event.data.plan, state.stepStates),
      };
    case "step_started": {
      const current = next.stepStates[event.data.step_id];
      return {
        ...next,
        phase: "running",
        status: "running",
        stepStates: {
          ...next.stepStates,
          [event.data.step_id]: {
            stepId: event.data.step_id,
            primitive: event.data.primitive,
            status: "running",
            startedAt: event.data.started_at ?? current?.startedAt ?? null,
            durationMs: current?.durationMs ?? null,
            resultIds: current?.resultIds ?? [],
          },
        },
      };
    }
    case "fact_created":
      return {
        ...next,
        facts: next.facts.some((fact) => fact.fact_id === event.data.fact.fact_id)
          ? next.facts
          : [...next.facts, event.data.fact],
      };
    case "analysis_note_created":
      return {
        ...next,
        notes: next.notes.some((note) => note.note_id === event.data.note.note_id)
          ? next.notes
          : [...next.notes, event.data.note],
        limitations: unique([...next.limitations, ...event.data.note.limitations]),
      };
    case "step_completed": {
      const current = next.stepStates[event.data.step_id];
      const primitive =
        current?.primitive ??
        next.plan?.steps.find((step) => step.step_id === event.data.step_id)?.primitive;
      if (!primitive) return next;
      return {
        ...next,
        stepStates: {
          ...next.stepStates,
          [event.data.step_id]: {
            stepId: event.data.step_id,
            primitive,
            status: event.data.status,
            startedAt: current?.startedAt ?? null,
            durationMs: event.data.duration_ms,
            resultIds: event.data.result_ids,
          },
        },
      };
    }
    case "validating":
    case "report_validating":
      return { ...next, phase: "validating", status: "running" };
    case "result": {
      const runReport = event.data.report;
      return {
        ...next,
        agentMode: event.data.agent_mode ?? next.agentMode,
        runReport,
        report: isGenericReport(runReport) ? null : runReport,
        selectedCustomerId: firstCustomerId(runReport),
        limitations: unique([...next.limitations, ...runReport.limitations]),
      };
    }
    case "fallback":
      return {
        ...next,
        agentMode: "fixture",
        fallbackReason: fallbackReason(event),
      };
    case "error":
      return {
        ...next,
        status: "failed",
        phase: "failed",
        error: event.data,
        suggestedQuestions: event.data.suggested_questions ?? [],
      };
    case "done":
      next.limitations = unique([
        ...next.limitations,
        ...(event.data.limitations ?? []),
      ]);
      if (event.data.status === "failed" || next.error) {
        return { ...next, status: "failed", phase: "failed" };
      }
      if (event.data.status === "degraded") {
        return { ...next, status: "degraded", phase: "degraded" };
      }
      if (!next.runReport) {
        return {
          ...next,
          status: "failed",
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
        status: next.fallbackReason ? "degraded" : "completed",
        phase: next.fallbackReason ? "degraded" : "completed",
      };
    case "plan":
    case "tool_started":
    case "tool_completed":
      return next;
  }
}

function hydrateArtifact(artifact: RunArtifact): RunState {
  const runReport = artifact.report;
  let stepStates = artifact.plan ? stepStatesForPlan(artifact.plan) : {};
  for (const note of artifact.notes) {
    const current = stepStates[note.step_id];
    if (current) {
      stepStates = {
        ...stepStates,
        [note.step_id]: {
          ...current,
          status: "completed",
          startedAt: note.started_at,
          durationMs: note.duration_ms,
          resultIds: note.result_ids,
        },
      };
    }
  }
  if (artifact.failed_step_id && stepStates[artifact.failed_step_id]) {
    stepStates = {
      ...stepStates,
      [artifact.failed_step_id]: {
        ...stepStates[artifact.failed_step_id],
        status: "failed",
      },
    };
  }

  return {
    ...initialRunState,
    runId: artifact.run_id,
    request: artifact.request,
    status: artifact.status,
    phase: phaseForStatus(artifact.status),
    report: runReport && !isGenericReport(runReport) ? runReport : null,
    runReport,
    goal: artifact.goal,
    clarification: artifact.clarification,
    plan: artifact.plan,
    stepStates,
    facts: artifact.facts,
    notes: artifact.notes,
    limitations: unique([
      ...artifact.limitations,
      ...artifact.notes.flatMap((note) => note.limitations),
      ...(runReport?.limitations ?? []),
    ]),
    suggestedQuestions: artifact.error?.suggested_questions ?? [],
    selectedCustomerId: firstCustomerId(runReport),
    error: artifact.error,
    lastEventId: artifact.last_event_id,
  };
}

function reportHasCustomer(state: RunState, customerId: string): boolean {
  return Boolean(
    state.runReport?.ranked_customers.some(
      (customer) => customer.customer_id === customerId,
    ),
  );
}

export function runReducer(state: RunState, action: RunAction): RunState {
  switch (action.kind) {
    case "start":
      return startState(action.runId, action.request);
    case "reset":
      return { ...initialRunState };
    case "hydrate_artifact":
      return hydrateArtifact(action.artifact);
    case "failed":
      if (state.runId !== action.runId) return state;
      return {
        ...state,
        status: "failed",
        phase: "failed",
        error: action.error,
        suggestedQuestions: action.error.suggested_questions ?? [],
      };
    case "clarification_submitted":
      if (
        state.runId !== action.runId ||
        state.clarification?.clarification_id !== action.clarificationId
      ) {
        return state;
      }
      return {
        ...state,
        status: "running",
        phase: "running",
        clarification: {
          ...state.clarification,
          answer: action.answer,
        },
      };
    case "select_customer":
      if (!reportHasCustomer(state, action.customerId)) return state;
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
