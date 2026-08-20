import type { SourceId } from "./contracts";

export interface SourceOption {
  id: SourceId;
  label: string;
  note: string;
}

export const KNOWN_SOURCE_OPTIONS: readonly SourceOption[] = [
  { id: "search_history", label: "검색 이력", note: "검색 행동과 결과" },
  { id: "search_feedback", label: "검색 피드백", note: "검색 결과 평가" },
  { id: "digital_behavior", label: "디지털 행동", note: "페이지와 Funnel 행동" },
  { id: "subscription", label: "가입 정보", note: "상품 가입과 변경 상태" },
  { id: "voc", label: "VOC", note: "고객센터 문의" },
];

const labels = new Map(
  KNOWN_SOURCE_OPTIONS.map((source) => [source.id, source.label] as const),
);

export function sourceLabel(sourceId: SourceId): string {
  return labels.get(sourceId) ?? sourceId;
}
