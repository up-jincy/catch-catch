import type { AnalysisFactPayload, GenericPrimitiveName } from "./contracts";

export const primitiveLabels: Record<GenericPrimitiveName, string> = {
  catalog_sources: "Source 카탈로그 확인",
  profile_events: "이벤트 분포 프로파일링",
  aggregate_events: "이벤트 집계",
  segment_customers: "고객 Segment 분할",
  detect_repetition: "반복 행동 탐지",
  match_sequence: "행동 순서 매칭",
  compare_segments: "Segment 비교",
  rank_customers: "고객 우선순위 산정",
  get_customer_journey: "대표 Journey 조회",
  get_evidence: "Evidence 조회",
};

export function primitiveLabel(primitive: string): string {
  return primitiveLabels[primitive as GenericPrimitiveName] ?? primitive;
}

const parameterLabels: Record<string, string> = {
  aggregation: "집계 방식",
  group_by: "그룹 기준",
  predicates: "필터 조건",
  sequence: "탐지할 행동 순서",
  time_grain: "시간 단위",
  measure: "측정 대상",
  metric_key: "지표 키",
  minimum_matching_events: "최소 매칭 이벤트",
  within_hours: "시간 창(시간)",
  top_n: "상위 N",
  customer_id: "대상 고객",
  segment_id: "대상 Segment",
  evidence_ids: "대상 Evidence",
  baseline_step_id: "기준 단계",
  comparison_step_id: "비교 단계",
};

function renderValue(key: string, value: unknown): string {
  if (Array.isArray(value)) {
    const items = value.map((item) =>
      typeof item === "string" ? item : JSON.stringify(item),
    );
    return key === "sequence" ? items.join(" → ") : items.join("; ");
  }
  if (typeof value === "object" && value !== null) return JSON.stringify(value);
  return String(value);
}

export function describeParameters(
  parameters: Record<string, unknown>,
  sourceIds: readonly string[],
): string {
  const parts = [`대상 Source ${sourceIds.join(", ")}`];
  for (const [key, value] of Object.entries(parameters)) {
    if (key === "primitive" || value === null || value === undefined) continue;
    if (Array.isArray(value) && !value.length) continue;
    if (typeof value === "object" && !Array.isArray(value) && !Object.keys(value).length) {
      continue;
    }
    parts.push(`${parameterLabels[key] ?? key} ${renderValue(key, value)}`);
  }
  if (parts.length === 1) parts.push("추가 조건 없음");
  return parts.join(" · ");
}

interface CountBucket {
  dimensions?: Record<string, unknown>;
  event_count?: number;
  customer_count?: number;
}

function dimensionText(dimensions: Record<string, unknown> | undefined): string {
  if (!dimensions) return "";
  return Object.entries(dimensions)
    .map(([key, value]) => `${key}=${String(value)}`)
    .join(", ");
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

export function payloadHighlights(payload: AnalysisFactPayload): string[] {
  const highlights: string[] = [];
  switch (payload.kind) {
    case "catalog_sources": {
      const sources = asArray(payload.sources)
        .map((source) => {
          const record = source as { source_id?: string; row_count?: number };
          return record.source_id ? `${record.source_id}(${record.row_count ?? 0}행)` : null;
        })
        .filter(Boolean);
      if (sources.length) highlights.push(`사용 가능 Source: ${sources.join(", ")}`);
      break;
    }
    case "profile_events":
      for (const bucket of asArray(payload.distributions).slice(0, 3) as CountBucket[]) {
        highlights.push(
          `분포 ${dimensionText(bucket.dimensions)}: 이벤트 ${bucket.event_count ?? 0}건, 고객 ${bucket.customer_count ?? 0}명`,
        );
      }
      break;
    case "aggregate_events":
      for (const bucket of asArray(payload.buckets).slice(0, 3) as CountBucket[]) {
        highlights.push(
          `집계 ${dimensionText(bucket.dimensions)}: 이벤트 ${bucket.event_count ?? 0}건, 고객 ${bucket.customer_count ?? 0}명`,
        );
      }
      break;
    case "segment_customers": {
      highlights.push(`Segment 고객 ${asArray(payload.customer_ids).length}명`);
      const counts = (payload.predicate_counts ?? {}) as Record<string, number>;
      for (const [predicate, count] of Object.entries(counts)) {
        highlights.push(`조건 '${predicate}' 충족: ${count}건`);
      }
      break;
    }
    case "detect_repetition":
      highlights.push(`반복 행동이 확인된 고객 ${asArray(payload.matches).length}명`);
      break;
    case "match_sequence":
      highlights.push(
        `요청한 행동 순서와 일치한 고객 ${asArray(payload.matched_customer_ids).length}명`,
      );
      break;
    case "compare_segments":
      for (const delta of asArray(payload.deltas) as Array<{
        metric_key?: string;
        baseline?: number;
        comparison?: number;
        delta?: number;
        unit?: string;
      }>) {
        highlights.push(
          `비교 ${delta.metric_key}: 기준 ${delta.baseline} → 비교 ${delta.comparison} (차이 ${delta.delta} ${delta.unit ?? ""})`,
        );
      }
      break;
    case "rank_customers": {
      const customers = asArray(payload.customers) as Array<{
        customer_id?: string;
        score?: number;
      }>;
      highlights.push(`우선순위가 산정된 고객 ${customers.length}명`);
      for (const customer of customers.slice(0, 3)) {
        highlights.push(`상위 고객 ${customer.customer_id}: score ${customer.score}`);
      }
      break;
    }
    case "get_customer_journey":
      highlights.push(
        `고객 ${String(payload.customer_id ?? "")}의 Journey 이벤트 ${asArray(payload.events).length}건 조회`,
      );
      break;
    case "get_evidence":
      highlights.push(`원본 Evidence ${asArray(payload.records).length}건 확인`);
      break;
  }
  return highlights;
}
