/**
 * 신청서(docs/spec.md) MVP Scenario를 그대로 옮긴 Mock Dataset.
 * AI검색 기획자가 로밍 관련 고객 행동을 분석하는 상황 하나에 집중한다.
 * STEP 6에서 실제 Run Controller로 갈아끼울 때 이 모양을 그대로 만들어 주면 된다.
 */
import type { EvidenceRecord } from "../../customer-intelligence/contracts";

import type {
  CatchReport,
  EvidenceMap,
  JourneyLane,
  JourneyNode,
  SourceOption,
  Stage,
} from "./types";

export const DEMO_QUESTION =
  "최근 로밍을 알아보는 고객들이 가장 많이 궁금해하는 게 뭐야?";

/** 첫 화면 입력창에서 타자 치듯 바뀌는 문구. 페르소나별로 하나씩. */
export const PLACEHOLDER_QUESTIONS = [
  "최근 로밍을 알아보는 고객들이 가장 많이 궁금해하는 게 뭐야?",
  "AI검색에서 원하는 답을 찾지 못한 고객은 그 다음에 어디로 갔어?",
  "같은 내용을 반복해서 검색하는 고객들은 뭘 찾고 있어?",
  "상담 전에 고객들이 어떤 경로를 거쳐 들어오는지 알려줘",
] as const;

/** 입력창 아래 제안 시나리오 카드. */
export const SUGGESTIONS = [
  {
    persona: "AI검색 기획",
    label: "로밍 고객의 진짜 질문",
    question: DEMO_QUESTION,
  },
  {
    persona: "AI검색 기획",
    label: "검색 실패 후 행동",
    question: "AI검색에서 원하는 답을 찾지 못한 고객은 그 다음에 어디로 이동했어?",
  },
  {
    persona: "CX",
    label: "반복 탐색 여정",
    question: "AI검색 후 다른 메뉴까지 계속 탐색한 고객 여정 보여줘.",
  },
  {
    persona: "CRM",
    label: "해외여행 준비 고객군",
    question: "최근 해외여행을 준비하는 것으로 보이는 고객군 찾아줘.",
  },
] as const;

export const SOURCE_OPTIONS: readonly SourceOption[] = [
  {
    id: "search_history",
    label: "AI검색 이력",
    note: "검색어와 결과 노출 이력",
    topics: ["로밍", "요금제", "장애"],
    interval: "2026.08.05 – 08.19",
  },
  {
    id: "search_feedback",
    label: "검색 피드백",
    note: "검색 결과에 남긴 평가",
    topics: ["만족", "불만족"],
    interval: "2026.08.05 – 08.19",
  },
  {
    id: "digital_behavior",
    label: "GA 행동로그",
    note: "앱과 웹의 페이지 이동",
    topics: ["상품비교", "상세조회", "가입퍼널"],
    interval: "2026.08.05 – 08.19",
  },
  {
    id: "subscription",
    label: "가입 정보",
    note: "상품 가입과 변경 상태",
    topics: ["가입", "미가입", "해지"],
    interval: "2026.08.01 – 08.19",
  },
  {
    id: "voc",
    label: "상담 이력",
    note: "고객센터 문의와 STT 요약",
    topics: ["로밍", "요금", "가입방법"],
    interval: "2026.08.05 – 08.19",
  },
];

export const LANES: JourneyLane[] = [
  { id: "search_history", label: "AI검색" },
  { id: "search_feedback", label: "검색 피드백" },
  { id: "digital_behavior", label: "앱 행동" },
  { id: "subscription", label: "가입 정보" },
  { id: "voc", label: "상담" },
];

/**
 * 로딩 화면 스테퍼. label은 사람이 읽는 문장, event는 실제 Canonical Run Event 이름.
 * 분석가 모드에서 event를 함께 노출한다.
 */
export const STAGES: Stage[] = [
  { key: "goal", label: "분석 목표를 세우고 있어요", short: "목표", event: "goal_created", detail: null, status: "pending" },
  { key: "plan", label: "분석 계획을 짜고 있어요", short: "계획", event: "plan_created", detail: null, status: "pending" },
  { key: "analyze", label: "시그널을 캐치하고 있어요", short: "분석", event: "step_completed", detail: null, status: "pending" },
  { key: "insight", label: "인사이트를 뽑아내고 있어요", short: "인사이트", event: "analysis_note_created", detail: null, status: "pending" },
  { key: "verify", label: "근거를 하나씩 검증하고 있어요", short: "검증", event: "report_validating", detail: null, status: "pending" },
];

/**
 * 각 단계가 진행되는 동안 흘려보낼 실시간 문장. 실제 이벤트 payload 자리를 흉내낸다.
 * `meta`는 화면에 노출하지 않는다. 이 문장이 실제로 어떤 이벤트 payload 자리에
 * 대응하는지 적어 둔 STEP 6 매핑 참고값이다.
 * `ms`는 그 문장이 화면에 머무는 시간이다.
 * 목표·계획·분석은 기계가 훑는 속도로 흘려보내고,
 * 인사이트와 검증은 사람이 읽을 수 있게 잡아 둔다. 특히 마지막 검증 결과.
 */
export const STAGE_TICKS: Record<string, readonly { text: string; meta: string; ms: number }[]> = {
  goal: [
    { text: "질문의 의도를 해석하고 있어요", meta: "goal.objective", ms: 400 },
    { text: "로밍을 탐색한 고객을 모집단으로 잡았어요", meta: "population: customers", ms: 420 },
    { text: "분석 기간을 2026.08.05 – 08.19로 고정했어요", meta: "time_range", ms: 480 },
  ],
  plan: [
    { text: "6단계 분석 계획을 세웠어요", meta: "plan.steps = 6", ms: 380 },
    { text: "5개 데이터 소스를 계획에 연결했어요", meta: "source_ids = 5", ms: 480 },
  ],
  analyze: [
    { text: "AI검색 이력에서 로밍 검색을 모으는 중", meta: "aggregate_events · 12,480건", ms: 350 },
    { text: "같은 주제를 반복 검색한 고객을 추리는 중", meta: "build_segment · 328명", ms: 380 },
    { text: "앱 행동로그를 고객 단위로 잇는 중", meta: "match_sequence · 1,204건", ms: 380 },
    { text: "가입 상태를 대조하는 중", meta: "profile_customers · 328명", ms: 350 },
    { text: "상담 이력까지 연결하는 중", meta: "match_sequence · 41명", ms: 450 },
  ],
  insight: [
    { text: "고객들이 무엇을 결정하지 못했는지 해석하는 중", meta: "analysis_note", ms: 850 },
    { text: "다음 행동 후보를 정리하는 중", meta: "note.next_action", ms: 1250 },
  ],
  verify: [
    { text: "주장 14개를 원본 데이터와 대조하는 중", meta: "report_validating · 14 claims", ms: 1100 },
    // 우리 팀의 차별점이 드러나는 문장. 가장 오래 머문다.
    { text: "12개 통과, 2개는 근거 부족으로 제외했어요", meta: "fact_ids = 37", ms: 2400 },
  ],
};

const JOURNEY_SOURCE: readonly Omit<JourneyNode, "column">[] = [
  {
    event_id: "J-01",
    evidence_id: "E-8801",
    source_id: "search_history",
    lane: "search_history",
    occurred_at: "2026-08-12T09:14:00+09:00",
    event_type: "search",
    action: "검색",
    topic: "로밍",
    outcome: "결과 확인",
    text: "일본 로밍",
    intensity: 0.24,
    tone: "signal",
    insight: "여행 국가만 넣은 첫 탐색입니다. 아직 조건이 좁혀지지 않았습니다.",
  },
  {
    event_id: "J-02",
    evidence_id: "E-8802",
    source_id: "search_history",
    lane: "search_history",
    occurred_at: "2026-08-12T09:16:00+09:00",
    event_type: "search",
    action: "재검색",
    topic: "로밍 요금",
    outcome: "결과 확인",
    text: "일본 로밍 가격",
    intensity: 0.52,
    tone: "repeat",
    insight: "2분 만에 가격 조건을 덧붙여 다시 검색했습니다. 첫 결과가 충분하지 않았다는 신호입니다.",
  },
  {
    event_id: "J-03",
    evidence_id: "E-8803",
    source_id: "digital_behavior",
    lane: "digital_behavior",
    occurred_at: "2026-08-12T09:22:00+09:00",
    event_type: "page_view",
    action: "상품 비교",
    topic: "로밍 요금제",
    outcome: "비교 이탈",
    text: "로밍 요금제 비교 페이지 체류 3분 12초",
    intensity: 0.4,
    tone: "signal",
    insight: "검색에서 답을 못 얻고 앱으로 넘어와 직접 비교했습니다.",
  },
  {
    event_id: "J-04",
    evidence_id: "E-8804",
    source_id: "digital_behavior",
    lane: "digital_behavior",
    occurred_at: "2026-08-13T21:05:00+09:00",
    event_type: "page_view",
    action: "상세 조회",
    topic: "로밍 상품",
    outcome: "이탈",
    text: "상품 상세페이지 3회 열람",
    intensity: 0.68,
    tone: "repeat",
    insight: "하루 뒤 다시 들어와 같은 상세를 세 번 봤습니다. 결정을 미루고 있습니다.",
  },
  {
    event_id: "J-05",
    evidence_id: "E-8805",
    source_id: "subscription",
    lane: "subscription",
    occurred_at: "2026-08-13T21:11:00+09:00",
    event_type: "subscription_state",
    action: "가입 미완료",
    topic: "로밍",
    outcome: "미가입",
    text: "로밍 상품 미가입 상태 유지",
    intensity: 0.74,
    tone: "negative",
    insight: "가입 직전 단계에서 이탈했습니다. 상품을 못 고른 것이지 관심이 식은 게 아닙니다.",
  },
  {
    event_id: "J-06",
    evidence_id: "E-8806",
    source_id: "search_feedback",
    lane: "search_feedback",
    occurred_at: "2026-08-14T10:02:00+09:00",
    event_type: "feedback",
    action: "부정 피드백",
    topic: "로밍",
    outcome: "불만족",
    text: "검색 결과에 '도움이 안 됐어요' 표시",
    intensity: 0.92,
    tone: "negative",
    insight: "검색 경험 자체에 대한 명시적 불만입니다. 개선 지점이 특정됩니다.",
  },
  {
    event_id: "J-07",
    evidence_id: "E-8807",
    source_id: "search_history",
    lane: "search_history",
    occurred_at: "2026-08-17T19:40:00+09:00",
    event_type: "search",
    action: "재검색",
    topic: "로밍",
    outcome: "결과 확인",
    text: "4일 일본 로밍",
    intensity: 0.62,
    tone: "repeat",
    insight: "3일 뒤 여행 기간까지 넣어 다시 검색했습니다. 고객이 스스로 조건을 좁혀 왔습니다.",
  },
  {
    event_id: "J-08",
    evidence_id: "E-8808",
    source_id: "voc",
    lane: "voc",
    occurred_at: "2026-08-18T14:23:00+09:00",
    event_type: "voc",
    action: "상담 인입",
    topic: "로밍 상품 선택",
    outcome: "상담 종료",
    text: "\"며칠짜리 로밍을 써야 할지 모르겠어요\"",
    intensity: 1,
    tone: "negative",
    insight: "6일간의 자가 탐색 끝에 상담으로 넘어왔습니다. 셀프케어가 실패한 지점입니다.",
  },
];

export const JOURNEY: JourneyNode[] = JOURNEY_SOURCE.map((node, index) => ({
  ...node,
  column: index,
}));

const EVIDENCE_RECORDS: EvidenceRecord[] = [
  {
    evidence_id: "E-8801",
    source_id: "search_history",
    occurred_at: "2026-08-12T09:14:00+09:00",
    masked_customer_id: "C-04**",
    summary: "AI검색에 '일본 로밍'을 입력하고 결과 상위 3건을 확인했습니다.",
    raw_fields: { query: "일본 로밍", result_count: 12, clicked_rank: 2, session_id: "s-771**" },
  },
  {
    evidence_id: "E-8802",
    source_id: "search_history",
    occurred_at: "2026-08-12T09:16:00+09:00",
    masked_customer_id: "C-04**",
    summary: "2분 뒤 '일본 로밍 가격'으로 조건을 좁혀 다시 검색했습니다.",
    raw_fields: { query: "일본 로밍 가격", result_count: 9, clicked_rank: null, session_id: "s-771**" },
  },
  {
    evidence_id: "E-8803",
    source_id: "digital_behavior",
    occurred_at: "2026-08-12T09:22:00+09:00",
    masked_customer_id: "C-04**",
    summary: "로밍 요금제 비교 페이지에서 3분 12초 머물렀습니다.",
    raw_fields: { page: "/roaming/compare", dwell_seconds: 192, scroll_depth: 0.86 },
  },
  {
    evidence_id: "E-8804",
    source_id: "digital_behavior",
    occurred_at: "2026-08-13T21:05:00+09:00",
    masked_customer_id: "C-04**",
    summary: "로밍 상품 상세페이지를 3회 반복 열람했습니다.",
    raw_fields: { page: "/roaming/product/**", view_count: 3, dwell_seconds: 241 },
  },
  {
    evidence_id: "E-8805",
    source_id: "subscription",
    occurred_at: "2026-08-13T21:11:00+09:00",
    masked_customer_id: "C-04**",
    summary: "가입 퍼널 마지막 단계 진입 후 가입을 완료하지 않았습니다.",
    raw_fields: { funnel_step: "confirm", completed: false, product_category: "roaming" },
  },
  {
    evidence_id: "E-8806",
    source_id: "search_feedback",
    occurred_at: "2026-08-14T10:02:00+09:00",
    masked_customer_id: "C-04**",
    summary: "검색 결과에 부정 피드백을 남겼습니다.",
    raw_fields: { verdict: "unhelpful", query: "일본 로밍 가격", comment: null },
  },
  {
    evidence_id: "E-8807",
    source_id: "search_history",
    occurred_at: "2026-08-17T19:40:00+09:00",
    masked_customer_id: "C-04**",
    summary: "여행 기간을 포함한 '4일 일본 로밍'으로 재검색했습니다.",
    raw_fields: { query: "4일 일본 로밍", result_count: 7, clicked_rank: 1, session_id: "s-902**" },
  },
  {
    evidence_id: "E-8808",
    source_id: "voc",
    occurred_at: "2026-08-18T14:23:00+09:00",
    masked_customer_id: "C-04**",
    summary: "상담에서 여행 기간에 맞는 로밍 상품 선택을 문의했습니다.",
    raw_fields: {
      channel: "고객센터",
      stt_summary: "며칠짜리 로밍을 써야 할지 모르겠어요",
      category: "로밍 상품 선택",
    },
  },
];

export const EVIDENCE: EvidenceMap = Object.fromEntries(
  EVIDENCE_RECORDS.map((record) => [record.evidence_id, record]),
);

export const REPORT: CatchReport = {
  headline: "단기 일본 여행 로밍을 고르지 못한 고객",
  headlineCount: 328,
  headlineTrailer: null,
  segmentLabel: "로밍 상품 결정 지연 Segment",
  summary:
    "이 고객들은 '로밍' 정보를 원한 게 아니라 여행 기간과 목적지에 맞는 상품을 빠르게 결정하고 싶어 했습니다. " +
    "검색으로 시작해 앱에서 직접 비교했지만 조건에 맞는 상품을 특정하지 못했고, 평균 4.2일 뒤 같은 주제로 다시 돌아왔습니다.",
  metrics: [
    { metric_key: "signal_customers", label: "시그널 고객", value: 328, unit: "명", dimensions: {} },
    { metric_key: "avg_search_repeat", label: "평균 반복 검색", value: 3.4, unit: "회", dimensions: {} },
    { metric_key: "detail_view_no_join", label: "상세 조회 후 미가입", value: 71, unit: "%", dimensions: {} },
    { metric_key: "research_gap_days", label: "재검색까지", value: 4.2, unit: "일", dimensions: {} },
  ],
  signals: [
    {
      signal_key: "repeat_search",
      label: "일본 로밍 반복 검색",
      contribution: 0.34,
      metric_refs: ["avg_search_repeat"],
      evidence_ids: ["E-8801", "E-8802", "E-8807"],
    },
    {
      signal_key: "compare_behavior",
      label: "가격·데이터량 비교",
      contribution: 0.27,
      metric_refs: ["detail_view_no_join"],
      evidence_ids: ["E-8803", "E-8804"],
    },
    {
      signal_key: "detail_drop",
      label: "상세 조회 후 미가입",
      contribution: 0.24,
      metric_refs: ["detail_view_no_join"],
      evidence_ids: ["E-8805"],
    },
    {
      signal_key: "return_search",
      label: "기간 후 동일 주제 재검색",
      contribution: 0.15,
      metric_refs: ["research_gap_days"],
      evidence_ids: ["E-8807"],
    },
  ],
  journey: JOURNEY,
  lanes: LANES,
  findings: [
    {
      claimId: "CL-01",
      statement: "최근 14일간 로밍 관련 검색을 2회 이상 반복한 고객이 328명입니다.",
      verdict: "passed",
      rejectedReason: null,
      chain: {
        claim: "metric · signal_customers · gte 328",
        fact: "F-0142 — step S2 · distinct_count · plan rev 2",
        source: "search_history — adapter v1.2 · manifest v3",
        evidence: "E-8801 외 327건",
      },
      evidenceIds: ["E-8801", "E-8802"],
    },
    {
      claimId: "CL-02",
      statement: "이 중 71%가 상품 상세페이지를 본 뒤 가입까지 이어지지 않았습니다.",
      verdict: "passed",
      rejectedReason: null,
      chain: {
        claim: "metric · detail_view_no_join · gte 71",
        fact: "F-0148 — step S4 · rate · plan rev 2",
        source: "digital_behavior, subscription — adapter v1.1",
        evidence: "E-8804 외 232건",
      },
      evidenceIds: ["E-8804", "E-8805"],
    },
    {
      claimId: "CL-03",
      statement: "동일 주제로 다시 검색하기까지 평균 4.2일이 걸렸습니다.",
      verdict: "passed",
      rejectedReason: null,
      chain: {
        claim: "metric · research_gap_days · eq 4.2",
        fact: "F-0151 — step S2 · avg · plan rev 2",
        source: "search_history — adapter v1.2 · manifest v3",
        evidence: "E-8807 외 189건",
      },
      evidenceIds: ["E-8807"],
    },
    {
      claimId: "CL-04",
      statement: "검색 결과에 부정 피드백을 남긴 고객은 96명입니다.",
      verdict: "passed",
      rejectedReason: null,
      chain: {
        claim: "metric · negative_feedback_customers · gte 96",
        fact: "F-0155 — step S3 · distinct_count · plan rev 2",
        source: "search_feedback — adapter v1.0 · manifest v2",
        evidence: "E-8806 외 95건",
      },
      evidenceIds: ["E-8806"],
    },
    {
      claimId: "CL-05",
      statement: "상담으로 이동한 고객은 41명이고, 문의 주제의 68%가 기간별 상품 선택이었습니다.",
      verdict: "passed",
      rejectedReason: null,
      chain: {
        claim: "segment · voc_transfer · gte 41",
        fact: "F-0160 — step S5 · match_sequence · plan rev 2",
        source: "voc — adapter v1.3 · manifest v2",
        evidence: "E-8808 외 40건",
      },
      evidenceIds: ["E-8808"],
    },
    {
      claimId: "CL-13",
      statement: "이 고객들의 여행 목적지는 82%가 일본입니다.",
      verdict: "rejected",
      rejectedReason:
        "목적지는 검색어 텍스트에서 추정한 값이라 원본 필드로 검증할 수 없습니다. 리포트에서 제외했습니다.",
      chain: {
        claim: "metric · destination_share · gte 82",
        fact: "— 대응하는 Fact 없음",
        source: "search_history (텍스트 추정)",
        evidence: "—",
      },
      evidenceIds: [],
    },
    {
      claimId: "CL-14",
      statement: "로밍 가입 전환율이 전월 대비 12% 하락했습니다.",
      verdict: "rejected",
      rejectedReason:
        "비교 대상인 전월 데이터가 분석 기간(2026.08.05–08.19) 밖입니다. 근거 없이 추세를 말하지 않습니다.",
      chain: {
        claim: "metric · conversion_delta · lte -12",
        fact: "— 분석 범위 밖",
        source: "subscription",
        evidence: "—",
      },
      evidenceIds: [],
    },
  ],
  actions: [
    {
      actionId: "search_keyword",
      title: "상황형 추천검색어를 먼저 노출하기",
      reason:
        "고객이 '로밍'으로 시작해 스스로 '4일 일본 로밍'까지 조건을 좁혀 왔습니다. 그 조건을 처음부터 제안하면 탐색을 4.2일 단축할 수 있습니다.",
      evidenceIds: ["E-8801", "E-8802", "E-8807"],
      keywords: [
        { keyword: "일본 4일 로밍 추천", reason: "여행 기간 + 목적지를 모두 포함한 조합" },
        { keyword: "일본 여행 로밍 가격 비교", reason: "가격 비교 행동이 두 번째로 높은 시그널" },
        { keyword: "내 여행에 맞는 로밍 찾기", reason: "조건을 모르는 고객을 위한 진입 문구" },
      ],
    },
    {
      actionId: "content_improvement",
      title: "로밍 상세페이지에 기간 선택 가이드 추가",
      reason:
        "상세페이지를 3회 이상 반복 열람한 뒤 미가입으로 끝난 비율이 71%입니다. 기간별 추천이 결정 지점에 없습니다.",
      evidenceIds: ["E-8804", "E-8805"],
      keywords: null,
    },
    {
      actionId: "further_analysis",
      title: "상담 인입 전 셀프케어 콘텐츠 검토",
      reason:
        "상담으로 넘어온 41명의 문의 68%가 기간별 상품 선택입니다. 상담 전 단계에서 해결 가능한 주제입니다.",
      evidenceIds: ["E-8808"],
      keywords: null,
    },
  ],
  limitations: [
    "분석 기간은 2026.08.05 – 08.19이며 전월 대비 추세는 계산하지 않았습니다.",
    "여행 목적지는 검색어 텍스트 추정값이라 리포트 주장에서 제외했습니다.",
  ],
  planSteps: [
    { stepId: "S1", primitive: "aggregate_events", objective: "로밍 관련 검색 이력 집계", durationMs: 1840, revisedFrom: null },
    { stepId: "S2", primitive: "build_segment", objective: "반복 검색 고객 식별", durationMs: 2960, revisedFrom: null },
    { stepId: "S3", primitive: "aggregate_events", objective: "검색 피드백 연결", durationMs: 1320, revisedFrom: null },
    { stepId: "S4", primitive: "match_sequence", objective: "앱 행동과 가입 상태 대조", durationMs: 4210, revisedFrom: "S4 (rev 1: 가입 상태 미포함)" },
    { stepId: "S5", primitive: "match_sequence", objective: "상담 이력까지 연결", durationMs: 3180, revisedFrom: null },
    { stepId: "S6", primitive: "profile_customers", objective: "Segment 해석과 대표 Journey 선정", durationMs: 4890, revisedFrom: null },
  ],
  score: {
    claimsPassed: 12,
    claimsTotal: 14,
    evidenceCoverage: 100,
    steps: 6,
    sources: 5,
    planRevisions: 1,
    durationMs: 18400,
  },
  datasetVersion: "synthetic-20260819",
  adapterVersions: {
    search_history: "v1.2",
    search_feedback: "v1.0",
    digital_behavior: "v1.1",
    subscription: "v1.1",
    voc: "v1.3",
  },
};

export const CLARIFICATION = {
  clarificationId: "CQ-01",
  question: "로밍 '검색'만 볼까요, 앱에서 상품을 비교한 행동까지 함께 볼까요?",
  hint: "함께 보면 가입 직전에 이탈한 고객까지 잡을 수 있어요.",
};

export const DEGRADED_LIMITATIONS = [
  "상담 이력(voc) 어댑터가 응답하지 않아 41명의 상담 전환은 확인하지 못했습니다.",
  "검색 피드백(search_feedback)은 분석 기간 중 8/16까지만 적재되어 있습니다.",
];

export const UNSUPPORTED_SUGGESTIONS = [
  "최근 로밍을 알아보는 고객들이 가장 많이 궁금해하는 게 뭐야?",
  "AI검색에서 원하는 답을 찾지 못한 고객은 그 다음에 어디로 이동했어?",
  "상담 전에 고객들이 어떤 경로를 거쳐 들어오는지 알려줘",
];
