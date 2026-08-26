export type SourceId = string;
export type AgentMode = "fixture" | "gemini";
export type RunStatus =
  | "queued"
  | "running"
  | "awaiting_clarification"
  | "completed"
  | "degraded"
  | "failed";
/**
 * Run phases are intentionally open-ended. Older components only render the
 * phases they know, while artifact hydration and future server versions may
 * retain a newer status without rejecting the run.
 */
export type RunPhase = string;

export type Scalar = string | number | boolean | null;
export type JsonValue =
  | Scalar
  | JsonValue[]
  | { [key: string]: JsonValue };
export type JsonObject = { [key: string]: JsonValue };
export type DimensionValue = Scalar;

export type GenericPrimitiveName =
  | "catalog_sources"
  | "profile_events"
  | "aggregate_events"
  | "segment_customers"
  | "detect_repetition"
  | "match_sequence"
  | "compare_segments"
  | "rank_customers"
  | "get_customer_journey"
  | "get_evidence";

/** Legacy tool events are a compatibility channel and may carry new primitives. */
export type ToolName = string;

export type RefreshCadence = "static_demo" | "hourly" | "daily" | "weekly";

export interface PublicDimensionDescriptor {
  semantic_type: "category" | "boolean" | "identifier" | "text";
  description: string;
  allowed_values: string[] | null;
}

export interface PublicMeasureDescriptor {
  semantic_type: "integer" | "number";
  description: string;
  unit: string;
}

export interface PublicSourceManifest {
  source_id: SourceId;
  label: string;
  description: string;
  data_interval: TimeRange;
  refresh_cadence: RefreshCadence;
  supported_event_types: string[];
  supported_topics: string[];
  supported_outcomes: string[];
  dimensions: Record<string, PublicDimensionDescriptor>;
  measures: Record<string, PublicMeasureDescriptor>;
  capabilities: GenericPrimitiveName[];
  adapter_version: string;
  manifest_version: string;
}

export interface PublicSourceList {
  items: PublicSourceManifest[];
}

export interface TimeRange {
  start_at: string;
  end_at: string;
}

export interface SemanticFieldRef {
  field: string;
  field_kind: "canonical" | "dimension" | "measure";
  source_id: SourceId | null;
}

export interface PopulationSpec {
  entity: "customers";
  description: string;
}

export interface MeasureSpec {
  metric_key: string;
  label: string;
  aggregation: "count" | "distinct_count" | "sum" | "avg" | "min" | "max" | "rate";
  field: SemanticFieldRef | null;
  unit: string;
}

export interface AnalysisPredicate {
  field: SemanticFieldRef;
  operator:
    | "eq"
    | "ne"
    | "lt"
    | "lte"
    | "gt"
    | "gte"
    | "in"
    | "not_in"
    | "contains"
    | "is_null";
  value: DimensionValue | DimensionValue[] | null;
}

export interface SequenceSpec {
  steps: string[];
  within_hours: number;
}

export interface AnalysisGoal {
  kind: "goal";
  goal_id: string;
  objective: string;
  population: PopulationSpec;
  time_range: TimeRange;
  source_ids: SourceId[];
  measures: MeasureSpec[];
  group_by: SemanticFieldRef[];
  predicates: AnalysisPredicate[];
  sequence: SequenceSpec | null;
  output:
    | "profile"
    | "aggregate"
    | "segment"
    | "comparison"
    | "ranking"
    | "journey"
    | "evidence";
}

export interface ClarificationRequired {
  kind: "clarification";
  clarification_id: string;
  question: string;
}

export interface ClarificationRecord extends ClarificationRequired {
  answer: string | null;
  requested_at: string | null;
  answered_at: string | null;
}

export interface ClarificationAnswer {
  answer: string;
}

export interface UnsupportedAnalysis {
  kind: "unsupported";
  code:
    | "pii_request"
    | "raw_export"
    | "write_request"
    | "unsupported_statistic"
    | "out_of_scope";
  reason: string;
  suggested_questions: string[];
}

export interface StepLimits {
  max_input_events: number;
  max_output_rows: number;
  max_evidence: number;
  timeout_seconds: number;
}

export interface ExpectedOutputSpec {
  payload_kind: GenericPrimitiveName;
  required_metric_keys: string[];
}

export type StopCondition =
  | { kind: "continue" }
  | { kind: "stop_on_empty" }
  | {
      kind: "stop_on_metric";
      metric_key: string;
      operator: "eq" | "lt" | "lte" | "gt" | "gte";
      target: number;
    };

export interface AnalysisStep {
  step_id: string;
  primitive: GenericPrimitiveName;
  parameters: Record<string, unknown> & { primitive: GenericPrimitiveName };
  source_ids: SourceId[];
  input_step_ids: string[];
  expected_output: ExpectedOutputSpec;
  stop_condition: StopCondition;
  limits: StepLimits;
  selection_reason: string;
}

export interface AnalysisPlan {
  plan_id: string;
  revision: number;
  goal_id: string;
  steps: AnalysisStep[];
  rationale: string;
}

export interface ProcessingStats {
  scanned_events: number;
  matched_events: number;
  returned_rows: number;
}

export interface AnalysisMetricFact {
  metric_key: string;
  label: string;
  value: number;
  unit: string;
  dimensions: Record<string, DimensionValue>;
}

export interface FactProvenance {
  scope: TimeRange & { source_ids: SourceId[]; max_events: number };
  source_ids: SourceId[];
  adapter_versions: Record<SourceId, string>;
  manifest_versions: Record<SourceId, string>;
  dataset_version: string;
}

export interface AnalysisFactPayload {
  kind: GenericPrimitiveName;
  input_fact_ids: string[];
  processing: ProcessingStats;
  provenance: FactProvenance;
  metrics: AnalysisMetricFact[];
  [key: string]: unknown;
}

export interface AnalysisFact {
  fact_id: string;
  step_id: string;
  primitive: GenericPrimitiveName;
  result_id: string;
  source_ids: SourceId[];
  customer_ids: string[];
  evidence_ids: string[];
  metrics: AnalysisMetricFact[];
  payload: AnalysisFactPayload;
  created_at: string;
}

export interface FactRef {
  fact_id: string;
  plan_revision: number;
  result_id: string | null;
  metric_key: string | null;
  label: string | null;
  unit: string | null;
  dimensions: Record<string, DimensionValue> | null;
  segment_id: string | null;
  customer_id: string | null;
  source_id: SourceId | null;
  evidence_id: string | null;
}

export interface VerifiedClaim {
  claim_id: string;
  claim_type: "metric" | "segment" | "customer" | "source" | "evidence";
  subject: string;
  operator: "eq" | "ne" | "lt" | "lte" | "gt" | "gte" | "contains" | "in";
  target: JsonValue;
  fact_refs: FactRef[];
  rendered_text: string;
}

export interface AnalysisNote {
  note_id: string;
  step_id: string;
  status: "completed";
  objective: string;
  fact_ids: string[];
  claims: VerifiedClaim[];
  next_step_id: string | null;
  next_action: string;
  limitations: string[];
  source_ids: SourceId[];
  result_ids: string[];
  evidence_ids: string[];
  started_at: string;
  completed_at: string;
  duration_ms: number;
  plan_revision: number;
}

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
  step_id?: string | null;
  suggested_questions?: string[];
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

// Open vocabulary: per-source membership is enforced by the backend source manifest.
export type EventType = string;

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
  report_kind?: "legacy_journey";
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

export interface AnalysisSignal {
  signal_key: string;
  label: string;
  contribution: number;
  metric_refs: string[];
  evidence_ids: string[];
}

export interface AnalysisRankedCustomer {
  customer_id: string;
  score: number;
  signals: AnalysisSignal[];
  evidence_ids: string[];
}

export interface AnalysisFinding {
  claim: VerifiedClaim;
  statement: string;
  fact_ids: string[];
  evidence_ids: string[];
}

export interface AnalysisRecommendation {
  action_id: string;
  title: string;
  reason: string;
  claim_ids: string[];
  fact_ids: string[];
  evidence_ids: string[];
}

export interface AnalysisReportProvenance {
  fact_ids: string[];
  result_ids: string[];
  source_ids: SourceId[];
  dataset_versions: string[];
  adapter_versions: Record<SourceId, string>;
  manifest_versions: Record<SourceId, string>;
}

export interface CustomerSignalReport {
  report_kind: "customer_signal";
  goal: AnalysisGoal;
  headline: string;
  executive_summary: string;
  metrics: AnalysisMetricFact[];
  signals: AnalysisSignal[];
  ranked_customers: AnalysisRankedCustomer[];
  representative_journeys: JourneyEvent[];
  findings: AnalysisFinding[];
  recommendations: AnalysisRecommendation[];
  limitations: string[];
  provenance: AnalysisReportProvenance;
}

export type GenericOrLegacyReport = InsightReport | CustomerSignalReport;

export interface RunSnapshot {
  run_id: string;
  status: RunStatus;
  request: RunRequest;
  created_at: string;
  updated_at: string;
  agent_mode: AgentMode | null;
  report: GenericOrLegacyReport | null;
  error: RunError | null;
  plan_history: AnalysisPlan[];
  last_event_id?: number;
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
  | "run_started"
  | "goal_created"
  | "clarification_required"
  | "plan_created"
  | "plan_revised"
  | "step_started"
  | "fact_created"
  | "analysis_note_created"
  | "step_completed"
  | "report_validating"
  | "plan"
  | "tool_started"
  | "tool_completed"
  | "validating"
  | "result"
  | "error"
  | "fallback"
  | "done";

/**
 * Events understood by the existing AgentTrace surface. Keep this alias
 * narrow so that old exhaustive renderers remain source compatible.
 */
export type RunStreamEvent =
  | { id: number; type: "plan"; data: { steps: string[] } }
  | {
      id: number;
      type: "tool_started";
      data: { tool: ToolName; source: SourceId[] };
    }
  | {
      id: number;
      type: "tool_completed";
      data: {
        tool: ToolName;
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
      data: { agent_mode?: AgentMode; report: GenericOrLegacyReport };
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
      };
    }
  | {
      id: number;
      type: "done";
      data: {
        status: "completed" | "degraded" | "failed";
        limitations?: string[];
      };
    };

/** Generic planner/executor events retained by the analysis workspace. */
export type GenericRunStreamEvent =
  | { id: number; type: "run_started"; data: { status: RunStatus } }
  | { id: number; type: "goal_created"; data: { goal: AnalysisGoal } }
  | {
      id: number;
      type: "clarification_required";
      data: ClarificationRequired;
    }
  | {
      id: number;
      type: "plan_created" | "plan_revised";
      data: { plan: AnalysisPlan };
    }
  | {
      id: number;
      type: "step_started";
      data: {
        step_id: string;
        primitive: GenericPrimitiveName;
        /** Always present on decoded current events; optional for legacy local ledgers. */
        selection_reason?: string;
        started_at?: string;
        objective?: string;
      };
    }
  | {
      id: number;
      type: "fact_created";
      data: { step_id: string; fact: AnalysisFact };
    }
  | {
      id: number;
      type: "analysis_note_created";
      data: { note: AnalysisNote };
    }
  | {
      id: number;
      type: "step_completed";
      data: {
        step_id: string;
        status: "completed" | "degraded" | "failed";
        result_ids: string[];
        duration_ms: number;
      };
    }
  | {
      id: number;
      type: "report_validating";
      data: { fact_ids: string[]; result_ids: string[] };
    };

export type AnyRunStreamEvent = RunStreamEvent | GenericRunStreamEvent;

export interface RunEventEnvelope {
  run_id: string;
  type: RunEventType;
  payload: Record<string, JsonValue>;
}

export interface RunVersions {
  dataset_versions: string[];
  adapter_versions: Record<SourceId, string>;
  manifest_versions: Record<SourceId, string>;
  prompt_version: string | null;
  model_version: string | null;
}

export interface RunArtifact {
  schema_version: 1;
  run_id: string;
  status: RunStatus;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  request: RunRequest;
  goal: AnalysisGoal | null;
  clarification: ClarificationRecord | null;
  plan: AnalysisPlan | null;
  plan_history: AnalysisPlan[];
  facts: AnalysisFact[];
  notes: AnalysisNote[];
  report: GenericOrLegacyReport | null;
  last_event_id: number;
  versions: RunVersions;
  failed_step_id: string | null;
  limitations: string[];
  error: RunError | null;
}

export interface ArtifactSummary {
  run_id: string;
  status: RunStatus;
  question: string;
  headline: string;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  error_code: string | null;
}

export interface ArtifactListResponse {
  artifacts: ArtifactSummary[];
}

export interface ArtifactDocumentProvenance {
  fact_ids: string[];
  result_ids: string[];
  source_ids: SourceId[];
  evidence_ids: string[];
  dataset_versions: string[];
  adapter_versions: Record<SourceId, string>;
  manifest_versions: Record<SourceId, string>;
  prompt_version: string | null;
  model_version: string | null;
  last_event_id: number;
}

export interface ArtifactDocument {
  document_kind: "run_artifact";
  run_id: string;
  status: RunStatus;
  headline: string;
  question: string;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  scope: TimeRange & { source_ids: SourceId[] };
  goal: AnalysisGoal | null;
  clarification: ClarificationRecord | null;
  plan: AnalysisPlan | null;
  plan_history: AnalysisPlan[];
  facts: AnalysisFact[];
  notes: AnalysisNote[];
  report: GenericOrLegacyReport | null;
  provenance: ArtifactDocumentProvenance;
  limitations: string[];
  error: RunError | null;
}

export type RunDownloadFormat = "json" | "md";
