import type {
  AgentMode,
  AnalysisScope,
  AnyRunStreamEvent,
  ArtifactDocument,
  ArtifactListResponse,
  ClarificationAnswer,
  CustomerJourneyResult,
  EvidenceRecord,
  EvidenceResult,
  Finding,
  InsightReport,
  JourneyEvent,
  Metric,
  PublicSourceList,
  RankedCustomer,
  Recommendation,
  RunAccepted,
  RunArtifact,
  RunDownloadFormat,
  RunError,
  RunEventType,
  RunRequest,
  RunSnapshot,
  RunStreamEvent,
  Scalar,
  Signal,
  SignalContribution,
  SourceId,
  ToolName,
  ToolStats,
} from "./contracts";
import {
  decodeAnalysisFact,
  decodeAnalysisGoal,
  decodeAnalysisNote,
  decodeAnalysisPlan,
  decodeArtifactDocument,
  decodeArtifactListResponse,
  decodeGenericPrimitive,
  decodePublicSourceList,
  decodeRunArtifact,
  decodeRunError as decodePublicRunError,
  decodeRunReport,
  decodeRunStatus,
  decodeSourceId,
} from "./run-contract-decoders";
import {
  SseParseError,
  createSseParser,
  type ParsedSseEvent,
} from "./parse-sse";

export const DEFAULT_API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL?.trim() || "http://127.0.0.1:8000";

export type RunClientErrorCode =
  | "http_error"
  | "invalid_response"
  | "network_error"
  | "protocol_error"
  | "stream_ended";

export class RunClientError extends Error {
  readonly code: RunClientErrorCode;
  readonly status?: number;

  constructor(
    code: RunClientErrorCode,
    message: string,
    options?: { status?: number; cause?: unknown },
  ) {
    super(message);
    this.name = "RunClientError";
    this.code = code;
    this.status = options?.status;
    if (options && "cause" in options) {
      this.cause = options.cause;
    }
  }
}

export interface RunClientOptions {
  apiBaseUrl?: string;
  fetchImpl?: typeof fetch;
  maxReconnectAttempts?: number;
}

export interface StreamRunOptions {
  signal?: AbortSignal;
  lastEventId?: number;
  maxReconnectAttempts?: number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

class ContractValidationError extends Error {
  constructor(path: string) {
    super(`invalid field: ${path}`);
    this.name = "ContractValidationError";
  }
}

const AGENT_MODES = ["fixture", "gemini"] as const;
const RUN_STATUSES = [
  "queued",
  "running",
  "awaiting_clarification",
  "completed",
  "degraded",
  "failed",
] as const;
const EVENT_TYPES = [
  "search",
  "feedback",
  "digital_behavior",
  "subscription",
  "voc",
] as const;
const RISK_LEVELS = ["high", "medium", "low"] as const;
const CONFIDENCE_LEVELS = ["high", "medium", "low"] as const;
const ANALYSIS_TYPES = [
  "cohort",
  "journey",
  "funnel",
  "pain_point",
  "general",
] as const;
const ACTION_IDS = [
  "care_call",
  "network_diagnosis",
  "content_improvement",
  "funnel_improvement",
  "campaign_target",
  "further_analysis",
] as const;
const TOOL_NAMES = [
  "catalog_sources",
  "profile_events",
  "aggregate_events",
  "segment_customers",
  "detect_repetition",
  "match_sequence",
  "compare_segments",
  "match_journey_pattern",
  "rank_customers",
  "get_customer_journey",
  "get_evidence",
] as const satisfies readonly ToolName[];
const RUN_EVENT_TYPES = [
  "run_started",
  "goal_created",
  "clarification_required",
  "plan_created",
  "plan_revised",
  "step_started",
  "fact_created",
  "analysis_note_created",
  "step_completed",
  "report_validating",
  "plan",
  "tool_started",
  "tool_completed",
  "validating",
  "result",
  "error",
  "fallback",
  "done",
] as const satisfies readonly RunEventType[];

function invalid(path: string): never {
  throw new ContractValidationError(path);
}

function expectRecord(value: unknown, path: string): Record<string, unknown> {
  return isRecord(value) ? value : invalid(path);
}

function expectString(value: unknown, path: string): string {
  return typeof value === "string" ? value : invalid(path);
}

function expectId(value: unknown, path: string): string {
  const result = expectString(value, path);
  return result.trim() ? result : invalid(path);
}

function expectOneOf<const Values extends readonly string[]>(
  value: unknown,
  allowed: Values,
  path: string,
): Values[number] {
  if (typeof value !== "string") {
    return invalid(path);
  }
  const match = allowed.find((item) => item === value);
  return match ?? invalid(path);
}

function expectArray<T>(
  value: unknown,
  path: string,
  decode: (item: unknown, path: string) => T,
): T[] {
  if (!Array.isArray(value)) {
    return invalid(path);
  }
  return value.map((item, index) => decode(item, `${path}[${index}]`));
}

function expectStringArray(value: unknown, path: string): string[] {
  return expectArray(value, path, expectString);
}

function expectIdArray(value: unknown, path: string): string[] {
  return expectArray(value, path, expectId);
}

function expectFiniteNumber(value: unknown, path: string): number {
  return typeof value === "number" && Number.isFinite(value) ? value : invalid(path);
}

function expectScore(value: unknown, path: string): number {
  const score = expectFiniteNumber(value, path);
  return score >= 0 && score <= 100 ? score : invalid(path);
}

function expectNonnegativeInteger(value: unknown, path: string): number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0
    ? value
    : invalid(path);
}

function expectTimestamp(value: unknown, path: string): string {
  const timestamp = expectString(value, path);
  const hasOffset = /(?:Z|[+-]\d{2}:\d{2})$/i.test(timestamp);
  return hasOffset && Number.isFinite(Date.parse(timestamp)) ? timestamp : invalid(path);
}

function decodeSource(value: unknown, path: string): SourceId {
  return decodeSourceId(value, path);
}

function decodeAgentMode(value: unknown, path: string): AgentMode {
  return expectOneOf(value, AGENT_MODES, path);
}

function decodeRunError(value: unknown, path: string): RunError {
  return decodePublicRunError(value, path);
}

function decodeRunRequest(value: unknown, path: string): RunRequest {
  const record = expectRecord(value, path);
  const enabledSources = expectArray(
    record.enabled_sources,
    `${path}.enabled_sources`,
    decodeSource,
  );
  return {
    question: expectId(record.question, `${path}.question`),
    start_at: expectTimestamp(record.start_at, `${path}.start_at`),
    end_at: expectTimestamp(record.end_at, `${path}.end_at`),
    enabled_sources: enabledSources,
  };
}

function decodeSignal(value: unknown, path: string): Signal {
  const record = expectRecord(value, path);
  return {
    code: expectId(record.code, `${path}.code`),
    label: expectString(record.label, `${path}.label`),
    score: expectScore(record.score, `${path}.score`),
    evidence_ids: expectIdArray(record.evidence_ids, `${path}.evidence_ids`),
  };
}

function decodeJourneyEvent(value: unknown, path: string): JourneyEvent {
  const record = expectRecord(value, path);
  return {
    event_id: expectId(record.event_id, `${path}.event_id`),
    evidence_id: expectId(record.evidence_id, `${path}.evidence_id`),
    source_id: decodeSource(record.source_id, `${path}.source_id`),
    occurred_at: expectTimestamp(record.occurred_at, `${path}.occurred_at`),
    event_type: expectOneOf(record.event_type, EVENT_TYPES, `${path}.event_type`),
    action: expectString(record.action, `${path}.action`),
    topic: expectString(record.topic, `${path}.topic`),
    outcome: expectString(record.outcome, `${path}.outcome`),
    text: expectString(record.text, `${path}.text`),
  };
}

function decodeAnalysisScope(value: unknown, path: string): AnalysisScope {
  const record = expectRecord(value, path);
  return {
    start_at: expectTimestamp(record.start_at, `${path}.start_at`),
    end_at: expectTimestamp(record.end_at, `${path}.end_at`),
    enabled_sources: expectArray(
      record.enabled_sources,
      `${path}.enabled_sources`,
      decodeSource,
    ),
    population_description: expectString(
      record.population_description,
      `${path}.population_description`,
    ),
  };
}

function decodeMetric(value: unknown, path: string): Metric {
  const record = expectRecord(value, path);
  const metricValue = record.value;
  if (
    typeof metricValue !== "string" &&
    !(typeof metricValue === "number" && Number.isFinite(metricValue))
  ) {
    return invalid(`${path}.value`);
  }
  const unit = record.unit;
  if (unit !== null && typeof unit !== "string") {
    return invalid(`${path}.unit`);
  }
  return {
    label: expectString(record.label, `${path}.label`),
    value: metricValue,
    unit,
    result_id: expectId(record.result_id, `${path}.result_id`),
  };
}

function decodeFinding(value: unknown, path: string): Finding {
  const record = expectRecord(value, path);
  return {
    title: expectString(record.title, `${path}.title`),
    description: expectString(record.description, `${path}.description`),
    confidence: expectOneOf(
      record.confidence,
      CONFIDENCE_LEVELS,
      `${path}.confidence`,
    ),
    evidence_ids: expectIdArray(record.evidence_ids, `${path}.evidence_ids`),
  };
}

function decodeRecommendation(value: unknown, path: string): Recommendation {
  const record = expectRecord(value, path);
  return {
    action_id: expectOneOf(record.action_id, ACTION_IDS, `${path}.action_id`),
    title: expectString(record.title, `${path}.title`),
    reason: expectString(record.reason, `${path}.reason`),
    evidence_ids: expectIdArray(record.evidence_ids, `${path}.evidence_ids`),
  };
}

function decodeSignalContribution(value: unknown, path: string): SignalContribution {
  const record = expectRecord(value, path);
  return {
    source_id: decodeSource(record.source_id, `${path}.source_id`),
    score: expectScore(record.score, `${path}.score`),
    signals: expectArray(record.signals, `${path}.signals`, decodeSignal),
  };
}

function decodeRankedCustomer(value: unknown, path: string): RankedCustomer {
  const record = expectRecord(value, path);
  const lastEventAt = record.last_event_at;
  if (lastEventAt !== null && typeof lastEventAt !== "string") {
    return invalid(`${path}.last_event_at`);
  }
  return {
    customer_id: expectId(record.customer_id, `${path}.customer_id`),
    risk_score: expectScore(record.risk_score, `${path}.risk_score`),
    risk_level: expectOneOf(record.risk_level, RISK_LEVELS, `${path}.risk_level`),
    signals: expectArray(record.signals, `${path}.signals`, decodeSignal),
    evidence_ids: expectIdArray(record.evidence_ids, `${path}.evidence_ids`),
    last_event_at:
      lastEventAt === null
        ? null
        : expectTimestamp(lastEventAt, `${path}.last_event_at`),
  };
}

function decodeInsightReport(value: unknown, path: string): InsightReport {
  const record = expectRecord(value, path);
  return {
    analysis_type: expectOneOf(
      record.analysis_type,
      ANALYSIS_TYPES,
      `${path}.analysis_type`,
    ),
    scope: decodeAnalysisScope(record.scope, `${path}.scope`),
    headline: expectString(record.headline, `${path}.headline`),
    executive_summary: expectString(
      record.executive_summary,
      `${path}.executive_summary`,
    ),
    metrics: expectArray(record.metrics, `${path}.metrics`, decodeMetric),
    findings: expectArray(record.findings, `${path}.findings`, decodeFinding),
    signal_contributions: expectArray(
      record.signal_contributions,
      `${path}.signal_contributions`,
      decodeSignalContribution,
    ),
    ranked_customers: expectArray(
      record.ranked_customers,
      `${path}.ranked_customers`,
      decodeRankedCustomer,
    ),
    representative_journeys: expectArray(
      record.representative_journeys,
      `${path}.representative_journeys`,
      decodeJourneyEvent,
    ),
    representative_journey_ids: expectIdArray(
      record.representative_journey_ids,
      `${path}.representative_journey_ids`,
    ),
    recommendations: expectArray(
      record.recommendations,
      `${path}.recommendations`,
      decodeRecommendation,
    ),
    sources_used: expectArray(record.sources_used, `${path}.sources_used`, decodeSource),
    limitations: expectStringArray(record.limitations, `${path}.limitations`),
  };
}

function decodeToolStats(value: unknown, path: string): ToolStats {
  const record = expectRecord(value, path);
  return {
    scanned_rows: expectNonnegativeInteger(record.scanned_rows, `${path}.scanned_rows`),
    returned_rows: expectNonnegativeInteger(record.returned_rows, `${path}.returned_rows`),
  };
}

function decodeScalar(value: unknown, path: string): Scalar {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "boolean" ||
    (typeof value === "number" && Number.isFinite(value))
  ) {
    return value;
  }
  return invalid(path);
}

function decodeEvidenceRecord(value: unknown, path: string): EvidenceRecord {
  const record = expectRecord(value, path);
  const rawFields = expectRecord(record.raw_fields, `${path}.raw_fields`);
  return {
    evidence_id: expectId(record.evidence_id, `${path}.evidence_id`),
    source_id: decodeSource(record.source_id, `${path}.source_id`),
    occurred_at: expectTimestamp(record.occurred_at, `${path}.occurred_at`),
    masked_customer_id: expectString(
      record.masked_customer_id,
      `${path}.masked_customer_id`,
    ),
    summary: expectString(record.summary, `${path}.summary`),
    raw_fields: Object.fromEntries(
      Object.entries(rawFields).map(([key, item]) => [
        key,
        decodeScalar(item, `${path}.raw_fields.${key}`),
      ]),
    ),
  };
}

function decodeRunAccepted(value: unknown): RunAccepted {
  const record = expectRecord(value, "run");
  return {
    run_id: expectId(record.run_id, "run.run_id"),
    status_url: expectId(record.status_url, "run.status_url"),
    events_url: expectId(record.events_url, "run.events_url"),
  };
}

function decodeRunSnapshot(value: unknown): RunSnapshot {
  const record = expectRecord(value, "snapshot");
  const agentMode = record.agent_mode;
  const report = record.report;
  const error = record.error;
  return {
    run_id: expectId(record.run_id, "snapshot.run_id"),
    status: decodeRunStatus(record.status, "snapshot.status"),
    request: decodeRunRequest(record.request, "snapshot.request"),
    created_at: expectTimestamp(record.created_at, "snapshot.created_at"),
    updated_at: expectTimestamp(record.updated_at, "snapshot.updated_at"),
    agent_mode:
      agentMode === null ? null : decodeAgentMode(agentMode, "snapshot.agent_mode"),
    report:
      report === null
        ? null
        : decodeRunReport(report, "snapshot.report", decodeInsightReport),
    error: error === null ? null : decodeRunError(error, "snapshot.error"),
    plan_history:
      record.plan_history === undefined
        ? []
        : expectArray(
            record.plan_history,
            "snapshot.plan_history",
            decodeAnalysisPlan,
          ),
    ...(record.last_event_id === undefined
      ? {}
      : {
          last_event_id: expectNonnegativeInteger(
            record.last_event_id,
            "snapshot.last_event_id",
          ),
        }),
  };
}

function decodeCustomerJourney(value: unknown): CustomerJourneyResult {
  const record = expectRecord(value, "journey");
  return {
    result_id: expectId(record.result_id, "journey.result_id"),
    customer_id: expectId(record.customer_id, "journey.customer_id"),
    events: expectArray(record.events, "journey.events", decodeJourneyEvent),
    evidence_ids: expectIdArray(record.evidence_ids, "journey.evidence_ids"),
    stats: decodeToolStats(record.stats, "journey.stats"),
  };
}

function decodeEvidence(value: unknown): EvidenceResult {
  const record = expectRecord(value, "evidence");
  return {
    result_id: expectId(record.result_id, "evidence.result_id"),
    records: expectArray(record.records, "evidence.records", decodeEvidenceRecord),
    evidence_ids: expectIdArray(record.evidence_ids, "evidence.evidence_ids"),
    stats: decodeToolStats(record.stats, "evidence.stats"),
  };
}

function abortReason(signal: AbortSignal | undefined, fallback: unknown): unknown {
  if (!signal?.aborted) {
    return fallback;
  }
  return signal.reason ?? new DOMException("The operation was aborted", "AbortError");
}

function decodeEvent(
  parsed: ParsedSseEvent<unknown>,
  expectedRunId: string,
): AnyRunStreamEvent {
  try {
    const type = expectOneOf(parsed.type, RUN_EVENT_TYPES, "event.type");
    const envelope = expectRecord(parsed.data, "event.data");
    if (
      expectId(envelope.run_id, "event.data.run_id") !== expectedRunId ||
      expectOneOf(envelope.type, RUN_EVENT_TYPES, "event.data.type") !== type
    ) {
      return invalid("event.data.run_id");
    }
    const payload = expectRecord(envelope.payload, "event.data.payload");

    switch (type) {
      case "run_started":
        return {
          id: parsed.id,
          type,
          data: { status: decodeRunStatus(payload.status, "event.payload.status") },
        };
      case "goal_created":
        return {
          id: parsed.id,
          type,
          data: {
            goal: decodeAnalysisGoal(payload.goal, "event.payload.goal"),
          },
        };
      case "clarification_required":
        return {
          id: parsed.id,
          type,
          data: {
            kind: "clarification",
            clarification_id: expectId(
              payload.clarification_id,
              "event.payload.clarification_id",
            ),
            question: expectId(payload.question, "event.payload.question"),
          },
        };
      case "plan_created":
      case "plan_revised":
        return {
          id: parsed.id,
          type,
          data: {
            plan: decodeAnalysisPlan(payload.plan, "event.payload.plan"),
          },
        };
      case "step_started": {
        const data: Extract<AnyRunStreamEvent, { type: "step_started" }>["data"] = {
          step_id: expectId(payload.step_id, "event.payload.step_id"),
          primitive: decodeGenericPrimitive(
            payload.primitive,
            "event.payload.primitive",
          ),
          selection_reason: expectId(
            payload.selection_reason,
            "event.payload.selection_reason",
          ),
        };
        if (payload.started_at !== undefined) {
          data.started_at = expectTimestamp(
            payload.started_at,
            "event.payload.started_at",
          );
        }
        if (payload.objective !== undefined) {
          data.objective = expectString(payload.objective, "event.payload.objective");
        }
        return { id: parsed.id, type, data };
      }
      case "fact_created":
        return {
          id: parsed.id,
          type,
          data: {
            step_id: expectId(payload.step_id, "event.payload.step_id"),
            fact: decodeAnalysisFact(payload.fact, "event.payload.fact"),
          },
        };
      case "analysis_note_created":
        return {
          id: parsed.id,
          type,
          data: {
            note: decodeAnalysisNote(payload.note, "event.payload.note"),
          },
        };
      case "step_completed":
        return {
          id: parsed.id,
          type,
          data: {
            step_id: expectId(payload.step_id, "event.payload.step_id"),
            status: expectOneOf(
              payload.status,
              ["completed", "degraded", "failed"] as const,
              "event.payload.status",
            ),
            result_ids: expectIdArray(
              payload.result_ids,
              "event.payload.result_ids",
            ),
            duration_ms: expectFiniteNumber(
              payload.duration_ms,
              "event.payload.duration_ms",
            ),
          },
        };
      case "report_validating":
        return {
          id: parsed.id,
          type,
          data: {
            fact_ids:
              payload.fact_ids === undefined
                ? []
                : expectIdArray(payload.fact_ids, "event.payload.fact_ids"),
            result_ids:
              payload.result_ids === undefined
                ? []
                : expectIdArray(payload.result_ids, "event.payload.result_ids"),
          },
        };
      case "plan":
        return {
          id: parsed.id,
          type,
          data: { steps: expectStringArray(payload.steps, "event.payload.steps") },
        };
      case "tool_started":
        return {
          id: parsed.id,
          type,
          data: {
            tool: expectOneOf(payload.tool, TOOL_NAMES, "event.payload.tool"),
            source: expectArray(payload.source, "event.payload.source", decodeSource),
          },
        };
      case "tool_completed":
        return {
          id: parsed.id,
          type,
          data: {
            tool: expectOneOf(payload.tool, TOOL_NAMES, "event.payload.tool"),
            source: expectArray(payload.source, "event.payload.source", decodeSource),
            count: expectNonnegativeInteger(payload.count, "event.payload.count"),
            duration_ms: expectFiniteNumber(
              payload.duration_ms,
              "event.payload.duration_ms",
            ),
            result_id: expectId(payload.result_id, "event.payload.result_id"),
          },
        };
      case "validating":
        return {
          id: parsed.id,
          type,
          data: {
            result_ids: expectIdArray(payload.result_ids, "event.payload.result_ids"),
          },
        };
      case "result":
        return {
          id: parsed.id,
          type,
          data: {
            ...(payload.agent_mode === undefined
              ? {}
              : {
                  agent_mode: decodeAgentMode(
                    payload.agent_mode,
                    "event.payload.agent_mode",
                  ),
                }),
            report: decodeRunReport(
              payload.report,
              "event.payload.report",
              decodeInsightReport,
            ),
          },
        };
      case "error":
        return { id: parsed.id, type, data: decodeRunError(payload, "event.payload") };
      case "fallback": {
        const data: Extract<RunStreamEvent, { type: "fallback" }>["data"] = {};
        if (payload.reason !== undefined) {
          data.reason = expectString(payload.reason, "event.payload.reason");
        }
        if (payload.message !== undefined) {
          data.message = expectString(payload.message, "event.payload.message");
        }
        if (payload.from !== undefined) {
          data.from = decodeAgentMode(payload.from, "event.payload.from");
        }
        if (payload.to !== undefined) {
          data.to = decodeAgentMode(payload.to, "event.payload.to");
        }
        return { id: parsed.id, type, data };
      }
      case "done":
        return {
          id: parsed.id,
          type,
          data: {
            status: expectOneOf(
              payload.status,
              ["completed", "degraded", "failed"] as const,
              "event.payload.status",
            ),
            ...(payload.limitations === undefined
              ? {}
              : {
                  limitations: expectStringArray(
                    payload.limitations,
                    "event.payload.limitations",
                  ),
                }),
          },
        };
    }
  } catch (error) {
    if (error instanceof RunClientError) {
      throw error;
    }
    throw new RunClientError("protocol_error", "SSE event contract is invalid", {
      cause: error,
    });
  }
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (isRecord(body) && typeof body.detail === "string") {
      return body.detail;
    }
  } catch {
    // The status remains actionable even when an upstream body is not JSON.
  }
  return `API request failed with status ${response.status}`;
}

function validateReconnectAttempts(value: number): number {
  if (!Number.isInteger(value) || value < 0) {
    throw new RangeError("maxReconnectAttempts must be a non-negative integer");
  }
  return value;
}

export class RunClient {
  private readonly apiBaseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly maxReconnectAttempts: number;

  constructor(options: RunClientOptions = {}) {
    this.apiBaseUrl = (options.apiBaseUrl ?? DEFAULT_API_BASE_URL).replace(/\/+$/, "");
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
    this.maxReconnectAttempts = validateReconnectAttempts(
      options.maxReconnectAttempts ?? 2,
    );
  }

  async createRun(request: RunRequest, signal?: AbortSignal): Promise<RunAccepted> {
    return this.requestJson(
      "/api/runs",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
      },
      signal,
      decodeRunAccepted,
    );
  }

  async submitClarification(
    runId: string,
    answer: string,
    signal?: AbortSignal,
  ): Promise<RunAccepted> {
    return this.requestJson(
      `/api/runs/${encodeURIComponent(runId)}/clarification`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answer } satisfies ClarificationAnswer),
      },
      signal,
      decodeRunAccepted,
    );
  }

  async getRun(runId: string, signal?: AbortSignal): Promise<RunSnapshot> {
    return this.requestJson(
      `/api/runs/${encodeURIComponent(runId)}`,
      {},
      signal,
      decodeRunSnapshot,
    );
  }

  async listSources(signal?: AbortSignal): Promise<PublicSourceList> {
    return this.requestJson("/api/sources", {}, signal, decodePublicSourceList);
  }

  async listRunArtifacts(signal?: AbortSignal): Promise<ArtifactListResponse> {
    return this.requestJson(
      "/api/run-artifacts",
      {},
      signal,
      decodeArtifactListResponse,
    );
  }

  async getRunArtifact(runId: string, signal?: AbortSignal): Promise<RunArtifact> {
    return this.requestJson(
      `/api/run-artifacts/${encodeURIComponent(runId)}`,
      {},
      signal,
      (value) => decodeRunArtifact(value, decodeInsightReport),
    );
  }

  async getRunDocument(
    runId: string,
    signal?: AbortSignal,
  ): Promise<ArtifactDocument> {
    return this.requestJson(
      `/api/run-artifacts/${encodeURIComponent(runId)}/document`,
      {},
      signal,
      (value) => decodeArtifactDocument(value, decodeInsightReport),
    );
  }

  getRunDownloadUrl(runId: string, format: RunDownloadFormat): string {
    return `${this.apiBaseUrl}/api/run-artifacts/${encodeURIComponent(runId)}/download.${format}`;
  }

  jsonDownloadUrl(runId: string): string {
    return this.getRunDownloadUrl(runId, "json");
  }

  markdownDownloadUrl(runId: string): string {
    return this.getRunDownloadUrl(runId, "md");
  }

  async getJourney(
    runId: string,
    customerId: string,
    signal?: AbortSignal,
  ): Promise<CustomerJourneyResult> {
    return this.requestJson(
      `/api/runs/${encodeURIComponent(runId)}/customers/${encodeURIComponent(customerId)}/journey`,
      {},
      signal,
      decodeCustomerJourney,
    );
  }

  async getEvidence(
    runId: string,
    evidenceId: string,
    signal?: AbortSignal,
  ): Promise<EvidenceResult> {
    return this.requestJson(
      `/api/runs/${encodeURIComponent(runId)}/evidence/${encodeURIComponent(evidenceId)}`,
      {},
      signal,
      decodeEvidence,
    );
  }

  async *streamRunEvents(
    runId: string,
    options: StreamRunOptions = {},
  ): AsyncGenerator<AnyRunStreamEvent> {
    let cursor = options.lastEventId ?? 0;
    if (!Number.isInteger(cursor) || cursor < 0) {
      throw new RangeError("lastEventId must be a non-negative integer");
    }
    const maxReconnectAttempts = validateReconnectAttempts(
      options.maxReconnectAttempts ?? this.maxReconnectAttempts,
    );
    let reconnectAttempts = 0;

    while (true) {
      options.signal?.throwIfAborted();
      const headers: Record<string, string> = { Accept: "text/event-stream" };
      if (cursor > 0) {
        headers["Last-Event-ID"] = String(cursor);
      }

      let response: Response;
      try {
        response = await this.fetchImpl(
          `${this.apiBaseUrl}/api/runs/${encodeURIComponent(runId)}/events`,
          { headers, signal: options.signal },
        );
      } catch (error) {
        if (options.signal?.aborted) {
          throw abortReason(options.signal, error);
        }
        if (reconnectAttempts >= maxReconnectAttempts) {
          throw new RunClientError("network_error", "SSE 연결에 실패했습니다.", {
            cause: error,
          });
        }
        reconnectAttempts += 1;
        continue;
      }

      if (!response.ok) {
        throw new RunClientError("http_error", await errorMessage(response), {
          status: response.status,
        });
      }
      if (!response.body) {
        throw new RunClientError("invalid_response", "SSE 응답 본문이 없습니다.");
      }
      const contentType = response.headers.get("content-type") ?? "";
      if (!contentType.toLowerCase().startsWith("text/event-stream")) {
        throw new RunClientError("invalid_response", "SSE 응답 형식이 아닙니다.");
      }

      const parser = createSseParser<unknown>();
      const decoder = new TextDecoder();
      const reader = response.body.getReader();
      let terminal = false;
      let protocolFailure: unknown;

      try {
        while (true) {
          const { done, value } = await reader.read();
          const parsedEvents = done
            ? [
                ...parser.push(decoder.decode()),
                ...parser.finish(),
              ]
            : parser.push(decoder.decode(value, { stream: true }));

          for (const parsed of parsedEvents) {
            if (parsed.id <= cursor) {
              continue;
            }
            if (parsed.id !== cursor + 1) {
              throw new RunClientError(
                "protocol_error",
                `SSE event sequence skipped from ${cursor} to ${parsed.id}`,
              );
            }
            const event = decodeEvent(parsed, runId);
            cursor = event.id;
            yield event;
            if (event.type === "done") {
              terminal = true;
              return;
            }
          }

          if (done) {
            break;
          }
        }
      } catch (error) {
        if (options.signal?.aborted) {
          throw abortReason(options.signal, error);
        }
        protocolFailure = error;
      } finally {
        if (!terminal) {
          try {
            await reader.cancel(protocolFailure);
          } catch {
            // Preserve the original protocol/read failure for retry and reporting.
          }
        }
        reader.releaseLock();
      }

      if (terminal) {
        return;
      }
      if (reconnectAttempts >= maxReconnectAttempts) {
        if (protocolFailure instanceof RunClientError) {
          throw protocolFailure;
        }
        if (protocolFailure instanceof SseParseError) {
          throw new RunClientError("protocol_error", protocolFailure.message, {
            cause: protocolFailure,
          });
        }
        throw new RunClientError(
          "stream_ended",
          "SSE 연결이 완료 이벤트 전에 종료됐습니다.",
          { cause: protocolFailure },
        );
      }
      reconnectAttempts += 1;
    }
  }

  private async requestJson<T>(
    path: string,
    init: RequestInit,
    signal: AbortSignal | undefined,
    decode: (value: unknown) => T,
  ): Promise<T> {
    signal?.throwIfAborted();
    let response: Response;
    try {
      response = await this.fetchImpl(`${this.apiBaseUrl}${path}`, {
        ...init,
        signal,
      });
    } catch (error) {
      if (signal?.aborted) {
        throw abortReason(signal, error);
      }
      throw new RunClientError("network_error", "API 연결에 실패했습니다.", {
        cause: error,
      });
    }
    if (!response.ok) {
      throw new RunClientError("http_error", await errorMessage(response), {
        status: response.status,
      });
    }
    let body: unknown;
    try {
      body = await response.json();
    } catch (error) {
      throw new RunClientError("invalid_response", "API 응답이 올바른 JSON이 아닙니다.", {
        cause: error,
      });
    }
    try {
      return decode(body);
    } catch (error) {
      throw new RunClientError("invalid_response", "API 응답 계약이 올바르지 않습니다.", {
        cause: error,
      });
    }
  }
}
