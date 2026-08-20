import type {
  AnalysisFact,
  AnalysisFactPayload,
  AnalysisFinding,
  AnalysisGoal,
  AnalysisMetricFact,
  AnalysisNote,
  AnalysisPlan,
  AnalysisPredicate,
  AnalysisRankedCustomer,
  AnalysisRecommendation,
  AnalysisReportProvenance,
  AnalysisSignal,
  AnalysisStep,
  ArtifactDocument,
  ArtifactDocumentProvenance,
  ArtifactListResponse,
  ArtifactSummary,
  ClarificationRecord,
  CustomerSignalReport,
  DimensionValue,
  FactProvenance,
  FactRef,
  GenericOrLegacyReport,
  GenericPrimitiveName,
  InsightReport,
  JsonValue,
  MeasureSpec,
  ProcessingStats,
  PublicDimensionDescriptor,
  PublicMeasureDescriptor,
  PublicSourceList,
  PublicSourceManifest,
  RunArtifact,
  RunError,
  RunRequest,
  RunStatus,
  RunVersions,
  SemanticFieldRef,
  SourceId,
  StopCondition,
  TimeRange,
  VerifiedClaim,
} from "./contracts";

export class ContractValidationError extends Error {
  constructor(path: string) {
    super(`invalid field: ${path}`);
    this.name = "ContractValidationError";
  }
}

const RUN_STATUSES = [
  "queued",
  "running",
  "awaiting_clarification",
  "completed",
  "degraded",
  "failed",
] as const;
const GENERIC_PRIMITIVES = [
  "catalog_sources",
  "profile_events",
  "aggregate_events",
  "segment_customers",
  "detect_repetition",
  "match_sequence",
  "compare_segments",
  "rank_customers",
  "get_customer_journey",
  "get_evidence",
] as const;

function invalid(path: string): never {
  throw new ContractValidationError(path);
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function expectRecord(value: unknown, path: string): Record<string, unknown> {
  return isRecord(value) ? value : invalid(path);
}

export function expectString(value: unknown, path: string): string {
  return typeof value === "string" ? value : invalid(path);
}

export function expectId(value: unknown, path: string): string {
  const id = expectString(value, path);
  return id.trim() ? id : invalid(path);
}

export function expectNumber(value: unknown, path: string): number {
  return typeof value === "number" && Number.isFinite(value) ? value : invalid(path);
}

export function expectInteger(value: unknown, path: string): number {
  const number = expectNumber(value, path);
  return Number.isInteger(number) ? number : invalid(path);
}

export function expectNonnegativeInteger(value: unknown, path: string): number {
  const number = expectInteger(value, path);
  return number >= 0 ? number : invalid(path);
}

export function expectTimestamp(value: unknown, path: string): string {
  const timestamp = expectString(value, path);
  const hasOffset = /(?:Z|[+-]\d{2}:\d{2})$/i.test(timestamp);
  return hasOffset && Number.isFinite(Date.parse(timestamp)) ? timestamp : invalid(path);
}

export function expectOneOf<const Values extends readonly string[]>(
  value: unknown,
  allowed: Values,
  path: string,
): Values[number] {
  if (typeof value !== "string") return invalid(path);
  const match = allowed.find((item) => item === value);
  return match ?? invalid(path);
}

export function expectArray<T>(
  value: unknown,
  path: string,
  decode: (item: unknown, itemPath: string) => T,
): T[] {
  if (!Array.isArray(value)) return invalid(path);
  return value.map((item, index) => decode(item, `${path}[${index}]`));
}

export function expectStringArray(value: unknown, path: string): string[] {
  return expectArray(value, path, expectString);
}

export function expectIdArray(value: unknown, path: string): string[] {
  return expectArray(value, path, expectId);
}

export function decodeSourceId(value: unknown, path: string): SourceId {
  const sourceId = expectId(value, path);
  return /^[a-z][a-z0-9_]{1,63}$/.test(sourceId) ? sourceId : invalid(path);
}

export function decodeRunStatus(value: unknown, path: string): RunStatus {
  return expectOneOf(value, RUN_STATUSES, path);
}

export function decodeGenericPrimitive(
  value: unknown,
  path: string,
): GenericPrimitiveName {
  return expectOneOf(value, GENERIC_PRIMITIVES, path);
}

function decodePublicDimension(
  value: unknown,
  path: string,
): PublicDimensionDescriptor {
  const record = expectRecord(value, path);
  return {
    semantic_type: expectOneOf(
      record.semantic_type,
      ["category", "boolean", "identifier", "text"] as const,
      `${path}.semantic_type`,
    ),
    description: expectId(record.description, `${path}.description`),
    allowed_values:
      record.allowed_values === null || record.allowed_values === undefined
        ? null
        : expectIdArray(record.allowed_values, `${path}.allowed_values`),
  };
}

function decodePublicMeasure(
  value: unknown,
  path: string,
): PublicMeasureDescriptor {
  const record = expectRecord(value, path);
  return {
    semantic_type: expectOneOf(
      record.semantic_type,
      ["integer", "number"] as const,
      `${path}.semantic_type`,
    ),
    description: expectId(record.description, `${path}.description`),
    unit: expectId(record.unit, `${path}.unit`),
  };
}

function decodeDescriptorRecord<T>(
  value: unknown,
  path: string,
  decode: (item: unknown, itemPath: string) => T,
): Record<string, T> {
  const record = expectRecord(value, path);
  return Object.fromEntries(
    Object.entries(record).map(([key, item]) => [
      key,
      decode(item, `${path}.${key}`),
    ]),
  );
}

function decodePublicSource(
  value: unknown,
  path: string,
): PublicSourceManifest {
  const record = expectRecord(value, path);
  return {
    source_id: decodeSourceId(record.source_id, `${path}.source_id`),
    label: expectId(record.label, `${path}.label`),
    description: expectId(record.description, `${path}.description`),
    data_interval: decodeTimeRange(record.data_interval, `${path}.data_interval`),
    refresh_cadence: expectOneOf(
      record.refresh_cadence,
      ["static_demo", "hourly", "daily", "weekly"] as const,
      `${path}.refresh_cadence`,
    ),
    supported_event_types: expectIdArray(
      record.supported_event_types,
      `${path}.supported_event_types`,
    ),
    supported_topics: expectIdArray(
      record.supported_topics,
      `${path}.supported_topics`,
    ),
    supported_outcomes: expectIdArray(
      record.supported_outcomes,
      `${path}.supported_outcomes`,
    ),
    dimensions: decodeDescriptorRecord(
      record.dimensions,
      `${path}.dimensions`,
      decodePublicDimension,
    ),
    measures: decodeDescriptorRecord(
      record.measures,
      `${path}.measures`,
      decodePublicMeasure,
    ),
    capabilities: expectArray(
      record.capabilities,
      `${path}.capabilities`,
      decodeGenericPrimitive,
    ),
    adapter_version: expectId(record.adapter_version, `${path}.adapter_version`),
    manifest_version: expectId(
      record.manifest_version,
      `${path}.manifest_version`,
    ),
  };
}

export function decodePublicSourceList(value: unknown): PublicSourceList {
  const record = expectRecord(value, "sources");
  return {
    items: expectArray(record.items, "sources.items", decodePublicSource),
  };
}

function decodeNullable<T>(
  value: unknown,
  path: string,
  decode: (item: unknown, itemPath: string) => T,
): T | null {
  return value === null ? null : decode(value, path);
}

function decodeOptionalNullable<T>(
  value: unknown,
  path: string,
  decode: (item: unknown, itemPath: string) => T,
): T | null {
  return value === null || value === undefined ? null : decode(value, path);
}

function decodeJsonValue(value: unknown, path: string): JsonValue {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "boolean" ||
    (typeof value === "number" && Number.isFinite(value))
  ) {
    return value;
  }
  if (Array.isArray(value)) {
    return value.map((item, index) => decodeJsonValue(item, `${path}[${index}]`));
  }
  if (isRecord(value)) {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [
        key,
        decodeJsonValue(item, `${path}.${key}`),
      ]),
    );
  }
  return invalid(path);
}

function decodeDimension(value: unknown, path: string): DimensionValue {
  const decoded = decodeJsonValue(value, path);
  return Array.isArray(decoded) || isRecord(decoded) ? invalid(path) : decoded;
}

function decodeDimensionRecord(
  value: unknown,
  path: string,
): Record<string, DimensionValue> {
  const record = expectRecord(value, path);
  return Object.fromEntries(
    Object.entries(record).map(([key, item]) => [
      key,
      decodeDimension(item, `${path}.${key}`),
    ]),
  );
}

function decodeStringRecord(value: unknown, path: string): Record<string, string> {
  const record = expectRecord(value, path);
  return Object.fromEntries(
    Object.entries(record).map(([key, item]) => [
      key,
      expectString(item, `${path}.${key}`),
    ]),
  );
}

function decodeTimeRange(value: unknown, path: string): TimeRange {
  const record = expectRecord(value, path);
  return {
    start_at: expectTimestamp(record.start_at, `${path}.start_at`),
    end_at: expectTimestamp(record.end_at, `${path}.end_at`),
  };
}

function decodeFieldRef(value: unknown, path: string): SemanticFieldRef {
  const record = expectRecord(value, path);
  return {
    field: expectId(record.field, `${path}.field`),
    field_kind: expectOneOf(
      record.field_kind,
      ["canonical", "dimension", "measure"] as const,
      `${path}.field_kind`,
    ),
    source_id: decodeOptionalNullable(record.source_id, `${path}.source_id`, decodeSourceId),
  };
}

function decodeMeasure(value: unknown, path: string): MeasureSpec {
  const record = expectRecord(value, path);
  return {
    metric_key: expectId(record.metric_key, `${path}.metric_key`),
    label: expectId(record.label, `${path}.label`),
    aggregation: expectOneOf(
      record.aggregation,
      ["count", "distinct_count", "sum", "avg", "min", "max", "rate"] as const,
      `${path}.aggregation`,
    ),
    field: decodeOptionalNullable(record.field, `${path}.field`, decodeFieldRef),
    unit: expectId(record.unit, `${path}.unit`),
  };
}

function decodePredicate(value: unknown, path: string): AnalysisPredicate {
  const record = expectRecord(value, path);
  const rawValue = record.value;
  const predicateValue = Array.isArray(rawValue)
    ? rawValue.map((item, index) => decodeDimension(item, `${path}.value[${index}]`))
    : decodeDimension(rawValue, `${path}.value`);
  return {
    field: decodeFieldRef(record.field, `${path}.field`),
    operator: expectOneOf(
      record.operator,
      [
        "eq",
        "ne",
        "lt",
        "lte",
        "gt",
        "gte",
        "in",
        "not_in",
        "contains",
        "is_null",
      ] as const,
      `${path}.operator`,
    ),
    value: predicateValue,
  };
}

export function decodeAnalysisGoal(value: unknown, path = "goal"): AnalysisGoal {
  const record = expectRecord(value, path);
  const population = expectRecord(record.population, `${path}.population`);
  const sequence = record.sequence;
  return {
    kind: expectOneOf(record.kind, ["goal"] as const, `${path}.kind`),
    goal_id: expectId(record.goal_id, `${path}.goal_id`),
    objective: expectId(record.objective, `${path}.objective`),
    population: {
      entity: expectOneOf(
        population.entity,
        ["customers"] as const,
        `${path}.population.entity`,
      ),
      description: expectId(
        population.description,
        `${path}.population.description`,
      ),
    },
    time_range: decodeTimeRange(record.time_range, `${path}.time_range`),
    source_ids: expectArray(record.source_ids, `${path}.source_ids`, decodeSourceId),
    measures: expectArray(record.measures, `${path}.measures`, decodeMeasure),
    group_by: expectArray(record.group_by, `${path}.group_by`, decodeFieldRef),
    predicates: expectArray(record.predicates, `${path}.predicates`, decodePredicate),
    sequence:
      sequence === null
        ? null
        : (() => {
            const decoded = expectRecord(sequence, `${path}.sequence`);
            return {
              steps: expectIdArray(decoded.steps, `${path}.sequence.steps`),
              within_hours: expectNonnegativeInteger(
                decoded.within_hours,
                `${path}.sequence.within_hours`,
              ),
            };
          })(),
    output: expectOneOf(
      record.output,
      [
        "profile",
        "aggregate",
        "segment",
        "comparison",
        "ranking",
        "journey",
        "evidence",
      ] as const,
      `${path}.output`,
    ),
  };
}

function decodeStopCondition(value: unknown, path: string): StopCondition {
  const record = expectRecord(value, path);
  const kind = expectOneOf(
    record.kind,
    ["continue", "stop_on_empty", "stop_on_metric"] as const,
    `${path}.kind`,
  );
  if (kind !== "stop_on_metric") return { kind };
  return {
    kind,
    metric_key: expectId(record.metric_key, `${path}.metric_key`),
    operator: expectOneOf(
      record.operator,
      ["eq", "lt", "lte", "gt", "gte"] as const,
      `${path}.operator`,
    ),
    target: expectNumber(record.target, `${path}.target`),
  };
}

function decodeAnalysisStep(value: unknown, path: string): AnalysisStep {
  const record = expectRecord(value, path);
  const parameters = expectRecord(record.parameters, `${path}.parameters`);
  const expectedOutput = expectRecord(record.expected_output, `${path}.expected_output`);
  const limits = expectRecord(record.limits, `${path}.limits`);
  const primitive = decodeGenericPrimitive(record.primitive, `${path}.primitive`);
  const parameterPrimitive = decodeGenericPrimitive(
    parameters.primitive,
    `${path}.parameters.primitive`,
  );
  return {
    step_id: expectId(record.step_id, `${path}.step_id`),
    primitive,
    parameters: { ...parameters, primitive: parameterPrimitive },
    source_ids: expectArray(record.source_ids, `${path}.source_ids`, decodeSourceId),
    input_step_ids: expectIdArray(record.input_step_ids, `${path}.input_step_ids`),
    expected_output: {
      payload_kind: decodeGenericPrimitive(
        expectedOutput.payload_kind,
        `${path}.expected_output.payload_kind`,
      ),
      required_metric_keys: expectIdArray(
        expectedOutput.required_metric_keys,
        `${path}.expected_output.required_metric_keys`,
      ),
    },
    stop_condition: decodeStopCondition(record.stop_condition, `${path}.stop_condition`),
    limits: {
      max_input_events: expectNonnegativeInteger(
        limits.max_input_events,
        `${path}.limits.max_input_events`,
      ),
      max_output_rows: expectNonnegativeInteger(
        limits.max_output_rows,
        `${path}.limits.max_output_rows`,
      ),
      max_evidence: expectNonnegativeInteger(
        limits.max_evidence,
        `${path}.limits.max_evidence`,
      ),
      timeout_seconds: expectNumber(
        limits.timeout_seconds,
        `${path}.limits.timeout_seconds`,
      ),
    },
  };
}

export function decodeAnalysisPlan(value: unknown, path = "plan"): AnalysisPlan {
  const record = expectRecord(value, path);
  return {
    plan_id: expectId(record.plan_id, `${path}.plan_id`),
    revision: expectNonnegativeInteger(record.revision, `${path}.revision`),
    goal_id: expectId(record.goal_id, `${path}.goal_id`),
    steps: expectArray(record.steps, `${path}.steps`, decodeAnalysisStep),
  };
}

function decodeMetric(value: unknown, path: string): AnalysisMetricFact {
  const record = expectRecord(value, path);
  return {
    metric_key: expectId(record.metric_key, `${path}.metric_key`),
    label: expectId(record.label, `${path}.label`),
    value: expectNumber(record.value, `${path}.value`),
    unit: expectId(record.unit, `${path}.unit`),
    dimensions: decodeDimensionRecord(record.dimensions, `${path}.dimensions`),
  };
}

function decodeProcessing(value: unknown, path: string): ProcessingStats {
  const record = expectRecord(value, path);
  return {
    scanned_events: expectNonnegativeInteger(
      record.scanned_events,
      `${path}.scanned_events`,
    ),
    matched_events: expectNonnegativeInteger(
      record.matched_events,
      `${path}.matched_events`,
    ),
    returned_rows: expectNonnegativeInteger(
      record.returned_rows,
      `${path}.returned_rows`,
    ),
  };
}

function decodeFactProvenance(value: unknown, path: string): FactProvenance {
  const record = expectRecord(value, path);
  const scopeRecord = expectRecord(record.scope, `${path}.scope`);
  return {
    scope: {
      ...decodeTimeRange(scopeRecord, `${path}.scope`),
      source_ids: expectArray(
        scopeRecord.source_ids,
        `${path}.scope.source_ids`,
        decodeSourceId,
      ),
      max_events: expectNonnegativeInteger(
        scopeRecord.max_events,
        `${path}.scope.max_events`,
      ),
    },
    source_ids: expectArray(record.source_ids, `${path}.source_ids`, decodeSourceId),
    adapter_versions: decodeStringRecord(
      record.adapter_versions,
      `${path}.adapter_versions`,
    ),
    manifest_versions: decodeStringRecord(
      record.manifest_versions,
      `${path}.manifest_versions`,
    ),
    dataset_version: expectId(record.dataset_version, `${path}.dataset_version`),
  };
}

function decodeFactPayload(value: unknown, path: string): AnalysisFactPayload {
  const record = expectRecord(value, path);
  return {
    ...record,
    kind: decodeGenericPrimitive(record.kind, `${path}.kind`),
    input_fact_ids: expectIdArray(record.input_fact_ids, `${path}.input_fact_ids`),
    processing: decodeProcessing(record.processing, `${path}.processing`),
    provenance: decodeFactProvenance(record.provenance, `${path}.provenance`),
    metrics: expectArray(record.metrics, `${path}.metrics`, decodeMetric),
  };
}

export function decodeAnalysisFact(value: unknown, path = "fact"): AnalysisFact {
  const record = expectRecord(value, path);
  return {
    fact_id: expectId(record.fact_id, `${path}.fact_id`),
    step_id: expectId(record.step_id, `${path}.step_id`),
    primitive: decodeGenericPrimitive(record.primitive, `${path}.primitive`),
    result_id: expectId(record.result_id, `${path}.result_id`),
    source_ids: expectArray(record.source_ids, `${path}.source_ids`, decodeSourceId),
    customer_ids: expectIdArray(record.customer_ids, `${path}.customer_ids`),
    evidence_ids: expectIdArray(record.evidence_ids, `${path}.evidence_ids`),
    metrics: expectArray(record.metrics, `${path}.metrics`, decodeMetric),
    payload: decodeFactPayload(record.payload, `${path}.payload`),
    created_at: expectTimestamp(record.created_at, `${path}.created_at`),
  };
}

function decodeFactRef(value: unknown, path: string): FactRef {
  const record = expectRecord(value, path);
  return {
    fact_id: expectId(record.fact_id, `${path}.fact_id`),
    plan_revision: expectNonnegativeInteger(
      record.plan_revision,
      `${path}.plan_revision`,
    ),
    result_id: decodeOptionalNullable(record.result_id, `${path}.result_id`, expectId),
    metric_key: decodeOptionalNullable(record.metric_key, `${path}.metric_key`, expectId),
    label: decodeOptionalNullable(record.label, `${path}.label`, expectId),
    unit: decodeOptionalNullable(record.unit, `${path}.unit`, expectId),
    dimensions:
      record.dimensions === null || record.dimensions === undefined
        ? null
        : decodeDimensionRecord(record.dimensions, `${path}.dimensions`),
    segment_id: decodeOptionalNullable(record.segment_id, `${path}.segment_id`, expectId),
    customer_id: decodeOptionalNullable(
      record.customer_id,
      `${path}.customer_id`,
      expectId,
    ),
    source_id: decodeOptionalNullable(
      record.source_id,
      `${path}.source_id`,
      decodeSourceId,
    ),
    evidence_id: decodeOptionalNullable(
      record.evidence_id,
      `${path}.evidence_id`,
      expectId,
    ),
  };
}

function decodeClaim(value: unknown, path: string): VerifiedClaim {
  const record = expectRecord(value, path);
  return {
    claim_id: expectId(record.claim_id, `${path}.claim_id`),
    claim_type: expectOneOf(
      record.claim_type,
      ["metric", "segment", "customer", "source", "evidence"] as const,
      `${path}.claim_type`,
    ),
    subject: expectId(record.subject, `${path}.subject`),
    operator: expectOneOf(
      record.operator,
      ["eq", "ne", "lt", "lte", "gt", "gte", "contains", "in"] as const,
      `${path}.operator`,
    ),
    target: decodeJsonValue(record.target, `${path}.target`),
    fact_refs: expectArray(record.fact_refs, `${path}.fact_refs`, decodeFactRef),
    rendered_text: expectId(record.rendered_text, `${path}.rendered_text`),
  };
}

export function decodeAnalysisNote(value: unknown, path = "note"): AnalysisNote {
  const record = expectRecord(value, path);
  return {
    note_id: expectId(record.note_id, `${path}.note_id`),
    step_id: expectId(record.step_id, `${path}.step_id`),
    status: expectOneOf(record.status, ["completed"] as const, `${path}.status`),
    objective: expectId(record.objective, `${path}.objective`),
    fact_ids: expectIdArray(record.fact_ids, `${path}.fact_ids`),
    claims: expectArray(record.claims, `${path}.claims`, decodeClaim),
    next_step_id: decodeOptionalNullable(
      record.next_step_id,
      `${path}.next_step_id`,
      expectId,
    ),
    limitations: expectStringArray(record.limitations, `${path}.limitations`),
    source_ids: expectArray(record.source_ids, `${path}.source_ids`, decodeSourceId),
    result_ids: expectIdArray(record.result_ids, `${path}.result_ids`),
    evidence_ids: expectIdArray(record.evidence_ids, `${path}.evidence_ids`),
    started_at: expectTimestamp(record.started_at, `${path}.started_at`),
    completed_at: expectTimestamp(record.completed_at, `${path}.completed_at`),
    duration_ms: expectNonnegativeInteger(record.duration_ms, `${path}.duration_ms`),
    plan_revision: expectNonnegativeInteger(
      record.plan_revision,
      `${path}.plan_revision`,
    ),
  };
}

function decodeSignal(value: unknown, path: string): AnalysisSignal {
  const record = expectRecord(value, path);
  return {
    signal_key: expectId(record.signal_key, `${path}.signal_key`),
    label: expectId(record.label, `${path}.label`),
    contribution: expectNumber(record.contribution, `${path}.contribution`),
    metric_refs: expectIdArray(record.metric_refs, `${path}.metric_refs`),
    evidence_ids: expectIdArray(record.evidence_ids, `${path}.evidence_ids`),
  };
}

function decodeRankedCustomer(value: unknown, path: string): AnalysisRankedCustomer {
  const record = expectRecord(value, path);
  return {
    customer_id: expectId(record.customer_id, `${path}.customer_id`),
    score: expectNumber(record.score, `${path}.score`),
    signals: expectArray(record.signals, `${path}.signals`, decodeSignal),
    evidence_ids: expectIdArray(record.evidence_ids, `${path}.evidence_ids`),
  };
}

function decodeFinding(value: unknown, path: string): AnalysisFinding {
  const record = expectRecord(value, path);
  return {
    claim: decodeClaim(record.claim, `${path}.claim`),
    statement: expectId(record.statement, `${path}.statement`),
    fact_ids: expectIdArray(record.fact_ids, `${path}.fact_ids`),
    evidence_ids: expectIdArray(record.evidence_ids, `${path}.evidence_ids`),
  };
}

function decodeRecommendation(value: unknown, path: string): AnalysisRecommendation {
  const record = expectRecord(value, path);
  return {
    action_id: expectId(record.action_id, `${path}.action_id`),
    title: expectId(record.title, `${path}.title`),
    reason: expectId(record.reason, `${path}.reason`),
    claim_ids: expectIdArray(record.claim_ids, `${path}.claim_ids`),
    fact_ids: expectIdArray(record.fact_ids, `${path}.fact_ids`),
    evidence_ids: expectIdArray(record.evidence_ids, `${path}.evidence_ids`),
  };
}

function decodeReportProvenance(
  value: unknown,
  path: string,
): AnalysisReportProvenance {
  const record = expectRecord(value, path);
  return {
    fact_ids: expectIdArray(record.fact_ids, `${path}.fact_ids`),
    result_ids: expectIdArray(record.result_ids, `${path}.result_ids`),
    source_ids: expectArray(record.source_ids, `${path}.source_ids`, decodeSourceId),
    dataset_versions: expectIdArray(
      record.dataset_versions,
      `${path}.dataset_versions`,
    ),
    adapter_versions: decodeStringRecord(
      record.adapter_versions,
      `${path}.adapter_versions`,
    ),
    manifest_versions: decodeStringRecord(
      record.manifest_versions,
      `${path}.manifest_versions`,
    ),
  };
}

function decodeJourneyEvent(value: unknown, path: string) {
  const record = expectRecord(value, path);
  return {
    event_id: expectId(record.event_id, `${path}.event_id`),
    evidence_id: expectId(record.evidence_id, `${path}.evidence_id`),
    source_id: decodeSourceId(record.source_id, `${path}.source_id`),
    occurred_at: expectTimestamp(record.occurred_at, `${path}.occurred_at`),
    event_type: expectOneOf(
      record.event_type,
      ["search", "feedback", "digital_behavior", "subscription", "voc"] as const,
      `${path}.event_type`,
    ),
    action: expectString(record.action, `${path}.action`),
    topic: expectString(record.topic, `${path}.topic`),
    outcome: expectString(record.outcome, `${path}.outcome`),
    text: expectString(record.text, `${path}.text`),
  };
}

export function decodeCustomerSignalReport(
  value: unknown,
  path = "report",
): CustomerSignalReport {
  const record = expectRecord(value, path);
  return {
    report_kind: expectOneOf(
      record.report_kind,
      ["customer_signal"] as const,
      `${path}.report_kind`,
    ),
    goal: decodeAnalysisGoal(record.goal, `${path}.goal`),
    headline: expectId(record.headline, `${path}.headline`),
    executive_summary: expectId(
      record.executive_summary,
      `${path}.executive_summary`,
    ),
    metrics: expectArray(record.metrics, `${path}.metrics`, decodeMetric),
    signals: expectArray(record.signals, `${path}.signals`, decodeSignal),
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
    findings: expectArray(record.findings, `${path}.findings`, decodeFinding),
    recommendations: expectArray(
      record.recommendations,
      `${path}.recommendations`,
      decodeRecommendation,
    ),
    limitations: expectStringArray(record.limitations, `${path}.limitations`),
    provenance: decodeReportProvenance(record.provenance, `${path}.provenance`),
  };
}

export type LegacyReportDecoder = (value: unknown, path: string) => InsightReport;

export function decodeRunReport(
  value: unknown,
  path: string,
  decodeLegacyReport: LegacyReportDecoder,
): GenericOrLegacyReport {
  const record = expectRecord(value, path);
  if (record.report_kind === "customer_signal") {
    return decodeCustomerSignalReport(record, path);
  }
  if (record.report_kind === undefined || record.report_kind === "legacy_journey") {
    return decodeLegacyReport(record, path);
  }
  return invalid(`${path}.report_kind`);
}

export function decodeRunRequest(value: unknown, path: string): RunRequest {
  const record = expectRecord(value, path);
  return {
    question: expectId(record.question, `${path}.question`),
    start_at: expectTimestamp(record.start_at, `${path}.start_at`),
    end_at: expectTimestamp(record.end_at, `${path}.end_at`),
    enabled_sources: expectArray(
      record.enabled_sources,
      `${path}.enabled_sources`,
      decodeSourceId,
    ),
  };
}

export function decodeRunError(value: unknown, path: string): RunError {
  const record = expectRecord(value, path);
  const error: RunError = {
    code: expectId(record.code, `${path}.code`),
    message: expectString(record.message, `${path}.message`),
  };
  if (record.step_id !== undefined) {
    error.step_id = decodeNullable(record.step_id, `${path}.step_id`, expectId);
  }
  if (record.suggested_questions !== undefined) {
    error.suggested_questions = expectIdArray(
      record.suggested_questions,
      `${path}.suggested_questions`,
    );
  }
  return error;
}

function decodeClarification(value: unknown, path: string): ClarificationRecord {
  const record = expectRecord(value, path);
  return {
    kind: expectOneOf(record.kind, ["clarification"] as const, `${path}.kind`),
    clarification_id: expectId(
      record.clarification_id,
      `${path}.clarification_id`,
    ),
    question: expectId(record.question, `${path}.question`),
    answer: decodeOptionalNullable(record.answer, `${path}.answer`, expectId),
    requested_at: decodeOptionalNullable(
      record.requested_at,
      `${path}.requested_at`,
      expectTimestamp,
    ),
    answered_at: decodeOptionalNullable(
      record.answered_at,
      `${path}.answered_at`,
      expectTimestamp,
    ),
  };
}

function decodeVersions(value: unknown, path: string): RunVersions {
  const record = expectRecord(value, path);
  return {
    dataset_versions: expectIdArray(
      record.dataset_versions,
      `${path}.dataset_versions`,
    ),
    adapter_versions: decodeStringRecord(
      record.adapter_versions,
      `${path}.adapter_versions`,
    ),
    manifest_versions: decodeStringRecord(
      record.manifest_versions,
      `${path}.manifest_versions`,
    ),
    prompt_version: decodeOptionalNullable(
      record.prompt_version,
      `${path}.prompt_version`,
      expectId,
    ),
    model_version: decodeOptionalNullable(
      record.model_version,
      `${path}.model_version`,
      expectId,
    ),
  };
}

export function decodeRunArtifact(
  value: unknown,
  decodeLegacyReport: LegacyReportDecoder,
): RunArtifact {
  const path = "artifact";
  const record = expectRecord(value, path);
  if (record.schema_version !== 1) invalid(`${path}.schema_version`);
  return {
    schema_version: 1,
    run_id: expectId(record.run_id, `${path}.run_id`),
    status: decodeRunStatus(record.status, `${path}.status`),
    created_at: expectTimestamp(record.created_at, `${path}.created_at`),
    updated_at: expectTimestamp(record.updated_at, `${path}.updated_at`),
    completed_at: decodeOptionalNullable(
      record.completed_at,
      `${path}.completed_at`,
      expectTimestamp,
    ),
    request: decodeRunRequest(record.request, `${path}.request`),
    goal: decodeOptionalNullable(record.goal, `${path}.goal`, decodeAnalysisGoal),
    clarification: decodeOptionalNullable(
      record.clarification,
      `${path}.clarification`,
      decodeClarification,
    ),
    plan: decodeOptionalNullable(record.plan, `${path}.plan`, decodeAnalysisPlan),
    facts: expectArray(record.facts, `${path}.facts`, decodeAnalysisFact),
    notes: expectArray(record.notes, `${path}.notes`, decodeAnalysisNote),
    report:
      record.report === null
        ? null
        : decodeRunReport(record.report, `${path}.report`, decodeLegacyReport),
    last_event_id: expectNonnegativeInteger(
      record.last_event_id,
      `${path}.last_event_id`,
    ),
    versions: decodeVersions(record.versions, `${path}.versions`),
    failed_step_id: decodeOptionalNullable(
      record.failed_step_id,
      `${path}.failed_step_id`,
      expectId,
    ),
    limitations: expectStringArray(record.limitations, `${path}.limitations`),
    error: decodeOptionalNullable(record.error, `${path}.error`, decodeRunError),
  };
}

function decodeArtifactSummary(value: unknown, path: string): ArtifactSummary {
  const record = expectRecord(value, path);
  return {
    run_id: expectId(record.run_id, `${path}.run_id`),
    status: decodeRunStatus(record.status, `${path}.status`),
    question: expectId(record.question, `${path}.question`),
    headline: expectId(record.headline, `${path}.headline`),
    created_at: expectTimestamp(record.created_at, `${path}.created_at`),
    updated_at: expectTimestamp(record.updated_at, `${path}.updated_at`),
    completed_at: decodeOptionalNullable(
      record.completed_at,
      `${path}.completed_at`,
      expectTimestamp,
    ),
    error_code: decodeOptionalNullable(
      record.error_code,
      `${path}.error_code`,
      expectId,
    ),
  };
}

export function decodeArtifactListResponse(value: unknown): ArtifactListResponse {
  const record = expectRecord(value, "artifact_list");
  return {
    artifacts: expectArray(
      record.artifacts,
      "artifact_list.artifacts",
      decodeArtifactSummary,
    ),
  };
}

function decodeDocumentProvenance(
  value: unknown,
  path: string,
): ArtifactDocumentProvenance {
  const record = expectRecord(value, path);
  return {
    fact_ids: expectIdArray(record.fact_ids, `${path}.fact_ids`),
    result_ids: expectIdArray(record.result_ids, `${path}.result_ids`),
    source_ids: expectArray(record.source_ids, `${path}.source_ids`, decodeSourceId),
    evidence_ids: expectIdArray(record.evidence_ids, `${path}.evidence_ids`),
    dataset_versions: expectIdArray(
      record.dataset_versions,
      `${path}.dataset_versions`,
    ),
    adapter_versions: decodeStringRecord(
      record.adapter_versions,
      `${path}.adapter_versions`,
    ),
    manifest_versions: decodeStringRecord(
      record.manifest_versions,
      `${path}.manifest_versions`,
    ),
    prompt_version: decodeOptionalNullable(
      record.prompt_version,
      `${path}.prompt_version`,
      expectId,
    ),
    model_version: decodeOptionalNullable(
      record.model_version,
      `${path}.model_version`,
      expectId,
    ),
    last_event_id: expectNonnegativeInteger(
      record.last_event_id,
      `${path}.last_event_id`,
    ),
  };
}

export function decodeArtifactDocument(
  value: unknown,
  decodeLegacyReport: LegacyReportDecoder,
): ArtifactDocument {
  const path = "document";
  const record = expectRecord(value, path);
  const scope = expectRecord(record.scope, `${path}.scope`);
  return {
    document_kind: expectOneOf(
      record.document_kind,
      ["run_artifact"] as const,
      `${path}.document_kind`,
    ),
    run_id: expectId(record.run_id, `${path}.run_id`),
    status: decodeRunStatus(record.status, `${path}.status`),
    headline: expectId(record.headline, `${path}.headline`),
    question: expectId(record.question, `${path}.question`),
    created_at: expectTimestamp(record.created_at, `${path}.created_at`),
    updated_at: expectTimestamp(record.updated_at, `${path}.updated_at`),
    completed_at: decodeOptionalNullable(
      record.completed_at,
      `${path}.completed_at`,
      expectTimestamp,
    ),
    scope: {
      ...decodeTimeRange(scope, `${path}.scope`),
      source_ids: expectArray(
        scope.source_ids,
        `${path}.scope.source_ids`,
        decodeSourceId,
      ),
    },
    goal: decodeOptionalNullable(record.goal, `${path}.goal`, decodeAnalysisGoal),
    clarification: decodeOptionalNullable(
      record.clarification,
      `${path}.clarification`,
      decodeClarification,
    ),
    plan: decodeOptionalNullable(record.plan, `${path}.plan`, decodeAnalysisPlan),
    notes: expectArray(record.notes, `${path}.notes`, decodeAnalysisNote),
    report:
      record.report === null
        ? null
        : decodeRunReport(record.report, `${path}.report`, decodeLegacyReport),
    provenance: decodeDocumentProvenance(
      record.provenance,
      `${path}.provenance`,
    ),
    limitations: expectStringArray(record.limitations, `${path}.limitations`),
    error: decodeOptionalNullable(record.error, `${path}.error`, decodeRunError),
  };
}
