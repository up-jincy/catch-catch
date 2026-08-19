export type SourceId = "search_history" | "search_feedback" | "voc";
export type AgentMode = "fixture" | "gemini";
export type RunStatus = "queued" | "running" | "completed" | "failed";
export type RunPhase =
  | "idle"
  | "running"
  | "validating"
  | "completed"
  | "degraded"
  | "failed";

export type Scalar = string | number | boolean | null;
export type JsonValue =
  | Scalar
  | JsonValue[]
  | { [key: string]: JsonValue };

export interface RunRequest {
  question: string;
  start_at: string;
  end_at: string;
  enabled_sources: SourceId[];
}

export interface RunAccepted {
  run_id: string;
  status_url: string;
  events_url: string;
}

export interface RunError {
  code: string;
  message: string;
}

export interface AnalysisScope {
  start_at: string;
  end_at: string;
  enabled_sources: SourceId[];
  population_description: string;
}

export interface Metric {
  label: string;
  value: number | string;
  unit: string | null;
  result_id: string;
}

export interface Finding {
  title: string;
  description: string;
  confidence: "high" | "medium" | "low";
  evidence_ids: string[];
}

export interface Recommendation {
  action_id:
    | "care_call"
    | "network_diagnosis"
    | "content_improvement"
    | "funnel_improvement"
    | "campaign_target"
    | "further_analysis";
  title: string;
  reason: string;
  evidence_ids: string[];
}

export interface Signal {
  code: string;
  label: string;
  score: number;
  evidence_ids: string[];
}

export interface SignalContribution {
  source_id: SourceId;
  score: number;
  signals: Signal[];
}

export interface RankedCustomer {
  customer_id: string;
  risk_score: number;
  risk_level: "high" | "medium" | "low";
  signals: Signal[];
  evidence_ids: string[];
  last_event_at: string | null;
}

export type EventType = "search" | "feedback" | "voc";

export interface JourneyEvent {
  event_id: string;
  evidence_id: string;
  source_id: SourceId;
  occurred_at: string;
  event_type: EventType;
  action: string;
  topic: string;
  outcome: string;
  text: string;
}

export interface InsightReport {
  analysis_type: "cohort" | "journey" | "funnel" | "pain_point" | "general";
  scope: AnalysisScope;
  headline: string;
  executive_summary: string;
  metrics: Metric[];
  findings: Finding[];
  signal_contributions: SignalContribution[];
  ranked_customers: RankedCustomer[];
  representative_journeys: JourneyEvent[];
  representative_journey_ids: string[];
  recommendations: Recommendation[];
  sources_used: SourceId[];
  limitations: string[];
}

export interface RunSnapshot {
  run_id: string;
  status: RunStatus;
  request: RunRequest;
  created_at: string;
  updated_at: string;
  agent_mode: AgentMode | null;
  report: InsightReport | null;
  error: RunError | null;
}

export interface ToolStats {
  scanned_rows: number;
  returned_rows: number;
}

export interface CustomerJourneyResult {
  result_id: string;
  customer_id: string;
  events: JourneyEvent[];
  evidence_ids: string[];
  stats: ToolStats;
}

export interface EvidenceRecord {
  evidence_id: string;
  source_id: SourceId;
  occurred_at: string;
  masked_customer_id: string;
  summary: string;
  raw_fields: Record<string, Scalar>;
}

export interface EvidenceResult {
  result_id: string;
  records: EvidenceRecord[];
  evidence_ids: string[];
  stats: ToolStats;
}

export type RunEventType =
  | "plan"
  | "tool_started"
  | "tool_completed"
  | "validating"
  | "result"
  | "error"
  | "fallback"
  | "done";

export type RunStreamEvent =
  | { id: number; type: "plan"; data: { steps: string[] } }
  | {
      id: number;
      type: "tool_started";
      data: { tool: string; source: SourceId[] };
    }
  | {
      id: number;
      type: "tool_completed";
      data: {
        tool: string;
        source: SourceId[];
        count: number;
        duration_ms: number;
        result_id: string;
      };
    }
  | { id: number; type: "validating"; data: { result_ids: string[] } }
  | {
      id: number;
      type: "result";
      data: { agent_mode: AgentMode; report: InsightReport };
    }
  | { id: number; type: "error"; data: RunError }
  | {
      id: number;
      type: "fallback";
      data: {
        reason?: string;
        message?: string;
        from?: AgentMode;
        to?: AgentMode;
        [key: string]: JsonValue | undefined;
      };
    }
  | {
      id: number;
      type: "done";
      data: { status: "completed" | "failed" };
    };

export interface RunEventEnvelope {
  run_id: string;
  type: RunEventType;
  payload: Record<string, JsonValue>;
}
