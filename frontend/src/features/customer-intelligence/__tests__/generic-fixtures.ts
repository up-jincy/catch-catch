const startAt = "2026-07-20T00:00:00+09:00";
const endAt = "2026-08-19T00:00:00+09:00";
const createdAt = "2026-08-20T01:00:00Z";
const completedAt = "2026-08-20T01:00:01Z";

export const dynamicSourceId = "support_chat_v2";

export const genericRequest = {
  question: "부정 피드백이 많은 Topic을 알려줘",
  start_at: startAt,
  end_at: endAt,
  enabled_sources: [dynamicSourceId],
};

export const genericGoal = {
  kind: "goal",
  goal_id: "goal-negative-feedback",
  objective: "부정 피드백이 많은 Topic별 고객 신호를 비교한다.",
  population: {
    entity: "customers",
    description: "분석 기간에 피드백을 남긴 고객",
  },
  time_range: { start_at: startAt, end_at: endAt },
  source_ids: [dynamicSourceId],
  measures: [
    {
      metric_key: "negative_feedback_count",
      label: "부정 피드백 수",
      aggregation: "count",
      field: null,
      unit: "건",
    },
  ],
  group_by: [
    { field: "topic", field_kind: "canonical", source_id: dynamicSourceId },
  ],
  predicates: [],
  sequence: null,
  output: "aggregate",
};

const limits = {
  max_input_events: 1000,
  max_output_rows: 20,
  max_evidence: 5,
  timeout_seconds: 10,
};

function planStep(stepId: string, primitive: string, inputStepIds: string[] = []) {
  return {
    step_id: stepId,
    primitive,
    parameters: { primitive },
    source_ids: [dynamicSourceId],
    input_step_ids: inputStepIds,
    expected_output: {
      payload_kind: primitive,
      required_metric_keys: ["negative_feedback_count"],
    },
    stop_condition: { kind: "continue" },
    limits,
  };
}

export const genericPlan = {
  plan_id: "plan-negative-feedback",
  revision: 0,
  goal_id: genericGoal.goal_id,
  steps: [
    planStep("step-catalog", "catalog_sources"),
    planStep("step-aggregate", "aggregate_events"),
    planStep("step-ranking", "rank_customers", ["step-aggregate"]),
  ],
};

const metric = {
  metric_key: "negative_feedback_count",
  label: "부정 피드백 수",
  value: 12,
  unit: "건",
  dimensions: { topic: "로밍" },
};

export const genericFact = {
  fact_id: "fact-negative-feedback",
  step_id: "step-aggregate",
  primitive: "aggregate_events",
  result_id: "aggregate_events:negative-feedback",
  source_ids: [dynamicSourceId],
  customer_ids: [],
  evidence_ids: ["EVD-DYNAMIC-1"],
  metrics: [metric],
  payload: {
    kind: "aggregate_events",
    input_fact_ids: [],
    processing: {
      scanned_events: 100,
      matched_events: 12,
      returned_rows: 1,
    },
    provenance: {
      scope: {
        start_at: startAt,
        end_at: endAt,
        source_ids: [dynamicSourceId],
        max_events: 1000,
      },
      source_ids: [dynamicSourceId],
      adapter_versions: { [dynamicSourceId]: "adapter-v2" },
      manifest_versions: { [dynamicSourceId]: "manifest-v2" },
      dataset_version: "dataset-v2",
    },
    metrics: [metric],
    requested_metric_key: "negative_feedback_count",
    buckets: [],
    series: [],
  },
  created_at: createdAt,
};

const verifiedClaim = {
  claim_type: "metric",
  subject: "negative_feedback_count",
  operator: "eq",
  target: 12,
  fact_refs: [
    {
      fact_id: genericFact.fact_id,
      plan_revision: 0,
      result_id: genericFact.result_id,
      metric_key: "negative_feedback_count",
      label: "부정 피드백 수",
      unit: "건",
      dimensions: { topic: "로밍" },
      segment_id: null,
      customer_id: null,
      source_id: dynamicSourceId,
      evidence_id: null,
    },
  ],
  claim_id: "claim-aaaaaaaaaaaaaaaaaaaaaaaa",
  rendered_text: "로밍 Topic의 부정 피드백은 12건입니다.",
};

export const genericNote = {
  note_id: "note-bbbbbbbbbbbbbbbbbbbbbbbb",
  step_id: "step-aggregate",
  status: "completed",
  objective: "Topic별 부정 피드백을 집계한다.",
  fact_ids: [genericFact.fact_id],
  claims: [verifiedClaim],
  next_step_id: "step-ranking",
  limitations: ["합성 데이터만 포함합니다."],
  source_ids: [dynamicSourceId],
  result_ids: [genericFact.result_id],
  evidence_ids: ["EVD-DYNAMIC-1"],
  started_at: createdAt,
  completed_at: completedAt,
  duration_ms: 1000,
  plan_revision: 0,
};

export const genericReport = {
  report_kind: "customer_signal",
  goal: genericGoal,
  headline: "로밍 Topic에서 부정 피드백 12건을 확인했습니다.",
  executive_summary: "Topic별 집계 결과 로밍의 부정 신호가 가장 큽니다.",
  metrics: [metric],
  signals: [],
  ranked_customers: [],
  representative_journeys: [],
  findings: [
    {
      claim: verifiedClaim,
      statement: verifiedClaim.rendered_text,
      fact_ids: [genericFact.fact_id],
      evidence_ids: ["EVD-DYNAMIC-1"],
    },
  ],
  recommendations: [],
  limitations: ["합성 데이터만 포함합니다."],
  provenance: {
    fact_ids: [genericFact.fact_id],
    result_ids: [genericFact.result_id],
    source_ids: [dynamicSourceId],
    dataset_versions: ["dataset-v2"],
    adapter_versions: { [dynamicSourceId]: "adapter-v2" },
    manifest_versions: { [dynamicSourceId]: "manifest-v2" },
  },
};

export const genericArtifact = {
  schema_version: 1,
  run_id: "11111111-1111-4111-8111-111111111111",
  status: "completed",
  created_at: createdAt,
  updated_at: completedAt,
  completed_at: completedAt,
  request: genericRequest,
  goal: genericGoal,
  clarification: null,
  plan: genericPlan,
  facts: [genericFact],
  notes: [genericNote],
  report: genericReport,
  last_event_id: 9,
  versions: {
    dataset_versions: ["dataset-v2"],
    adapter_versions: { [dynamicSourceId]: "adapter-v2" },
    manifest_versions: { [dynamicSourceId]: "manifest-v2" },
    prompt_version: "prompt-v2",
    model_version: "gemini-3.7-flash",
  },
  failed_step_id: null,
  limitations: ["합성 데이터만 포함합니다."],
  error: null,
};

export const genericDocument = {
  document_kind: "run_artifact",
  run_id: genericArtifact.run_id,
  status: "completed",
  headline: genericReport.headline,
  question: genericRequest.question,
  created_at: createdAt,
  updated_at: completedAt,
  completed_at: completedAt,
  scope: {
    start_at: startAt,
    end_at: endAt,
    source_ids: [dynamicSourceId],
  },
  goal: genericGoal,
  clarification: null,
  plan: genericPlan,
  notes: [genericNote],
  report: genericReport,
  provenance: {
    fact_ids: [genericFact.fact_id],
    result_ids: [genericFact.result_id],
    source_ids: [dynamicSourceId],
    evidence_ids: ["EVD-DYNAMIC-1"],
    dataset_versions: ["dataset-v2"],
    adapter_versions: { [dynamicSourceId]: "adapter-v2" },
    manifest_versions: { [dynamicSourceId]: "manifest-v2" },
    prompt_version: "prompt-v2",
    model_version: "gemini-3.7-flash",
    last_event_id: 9,
  },
  limitations: genericArtifact.limitations,
  error: null,
};
