/**
 * 액션 적용 시나리오 Mock.
 *
 * 신청서 9장의 Action Preview 를 "적용하면 끝"이 아니라
 * "적용하고, 지켜보고, 예측이 맞았는지 채점한다"까지 확장한 데이터다.
 * 분석 단계에서 주장을 원본 데이터로 검증하듯 실행 단계에서는 예측을 실측으로 검증한다.
 *
 * 액션 하나(추천검색어)만 깊게 만든다. 셋을 다 만들면 전부 얕아진다.
 */
import type { ActionPlan } from "./types";

const SEARCH_KEYWORD_PLAN: ActionPlan = {
  actionId: "search_keyword",
  title: "상황형 추천검색어를 먼저 노출하기",
  segmentLabel: "로밍 상품 결정 지연",
  segmentSize: 328,
  applyLabel: "캐치한 대로 바꿔보기",
  observeDays: 7,

  /*
   * 실제 AI검색은 답변이 위, 입력창이 하단이고 추천검색어가 입력창 바로 위에 뜬다.
   * 픽셀까지 재현하지 않되 위치는 실제 구조를 따른다.
   * 어디에 뜨는지가 틀리면 시안이 전달하는 정보 자체가 거짓이 된다.
   */
  asIs: {
    label: "지금 AI검색 화면",
    context: "고객이 '로밍'을 검색했을 때",
    items: [
      { kind: "result", text: "로밍 요금제 안내", sub: "상품 소개", added: false },
      { kind: "result", text: "로밍 가입 방법", sub: "이용 가이드", added: false },
      { kind: "result", text: "해외 데이터 로밍이란?", sub: "용어 설명", added: false },
      { kind: "query", text: "로밍", sub: null, added: false },
    ],
  },

  toBe: {
    label: "바꾼 뒤 AI검색 화면",
    context: "고객이 '로밍'을 검색했을 때",
    items: [
      { kind: "result", text: "로밍 요금제 안내", sub: "상품 소개", added: false },
      { kind: "result", text: "로밍 가입 방법", sub: "이용 가이드", added: false },
      { kind: "result", text: "해외 데이터 로밍이란?", sub: "용어 설명", added: false },
      { kind: "suggestion", text: "일본 4일 로밍 추천", sub: "여행 기간 + 목적지", added: true },
      { kind: "suggestion", text: "일본 여행 로밍 가격 비교", sub: "비교 행동 2순위 시그널", added: true },
      { kind: "suggestion", text: "내 여행에 맞는 로밍 찾기", sub: "조건을 모르는 고객용", added: true },
      { kind: "query", text: "로밍", sub: null, added: false },
    ],
  },

  predictions: [
    {
      predictionId: "conversion",
      direction: "gain",
      label: "로밍 검색 → 가입 전환율",
      from: "12%",
      to: "18%",
      delta: "+6%p",
      reason: "고객이 스스로 좁혀 온 조건을 처음부터 제안하면 결정 지점이 앞당겨집니다.",
      evidenceIds: ["E-8801", "E-8807"],
    },
    {
      predictionId: "research_days",
      direction: "gain",
      label: "평균 탐색 기간",
      from: "4.2일",
      to: "2.1일",
      delta: "-2.1일",
      reason: "재검색까지 걸린 4.2일의 상당 부분이 조건을 좁히는 데 쓰였습니다.",
      evidenceIds: ["E-8802", "E-8807"],
    },
    {
      predictionId: "voc_transfer",
      direction: "gain",
      label: "상담 인입",
      from: "41명",
      to: "26명",
      delta: "-37%",
      reason: "상담 문의의 68%가 기간별 상품 선택입니다. 검색에서 해결되면 상담까지 오지 않습니다.",
      evidenceIds: ["E-8808"],
    },
    {
      predictionId: "other_products",
      direction: "risk",
      label: "다른 로밍 상품 노출",
      from: "100%",
      to: "77%",
      delta: "-23%",
      reason: "추천검색어 3개가 특정 조합에 쏠려 나머지 상품의 노출 기회가 줄어듭니다.",
      evidenceIds: ["E-8803"],
    },
    {
      predictionId: "non_japan",
      direction: "risk",
      label: "일본 외 목적지 검색 정확도",
      from: "84%",
      to: "76%",
      delta: "-8%p",
      reason: "로밍 고객의 31%는 일본이 아닙니다. 일본 중심 제안이 오히려 방해가 될 수 있습니다.",
      evidenceIds: ["E-8801"],
    },
    {
      predictionId: "query_variety",
      direction: "risk",
      label: "자연 검색어 다양성",
      from: "100%",
      to: "88%",
      delta: "-12%",
      reason: "고정 검색어를 먼저 보여주면 고객이 직접 입력하는 표현이 줄어 새 니즈 발견이 늦어집니다.",
      evidenceIds: ["E-8802"],
    },
  ],

  /** 7일간의 관찰. 타임랩스로 하루씩 감아 보여준다. */
  timelapse: [
    { day: 0, date: "8/19", values: { conversion: 12, research_days: 4.2, voc_transfer: 41 } },
    { day: 1, date: "8/20", values: { conversion: 13.1, research_days: 3.8, voc_transfer: 39 } },
    { day: 2, date: "8/21", values: { conversion: 14.4, research_days: 3.3, voc_transfer: 37 } },
    { day: 3, date: "8/22", values: { conversion: 15.2, research_days: 3.0, voc_transfer: 36 } },
    { day: 4, date: "8/23", values: { conversion: 16.0, research_days: 2.7, voc_transfer: 34 } },
    { day: 5, date: "8/24", values: { conversion: 16.6, research_days: 2.5, voc_transfer: 33 } },
    { day: 6, date: "8/25", values: { conversion: 16.9, research_days: 2.4, voc_transfer: 32 } },
    { day: 7, date: "8/26", values: { conversion: 17.0, research_days: 2.4, voc_transfer: 32 } },
  ],

  outcomes: [
    {
      predictionId: "conversion",
      actual: "17.0%",
      verdict: "hit",
      note: "예측 18%에 근접했습니다.",
    },
    {
      predictionId: "research_days",
      actual: "2.4일",
      verdict: "hit",
      note: "예측 2.1일보다 0.3일 길지만 방향과 폭이 맞았습니다.",
    },
    {
      predictionId: "voc_transfer",
      actual: "32명",
      verdict: "miss",
      note:
        "예측은 -37%였는데 실제는 -22%에 그쳤습니다. 검색 단계는 줄었지만 " +
        "상세페이지에서 여전히 이탈했습니다. 결정 지점에 기간별 안내가 없습니다.",
    },
    {
      predictionId: "other_products",
      actual: "-19%",
      verdict: "hit",
      note: "예측 -23%보다 완만했습니다.",
    },
    {
      predictionId: "non_japan",
      actual: "-14%p",
      verdict: "miss",
      note:
        "예측 -8%p보다 나빴습니다. 일본 외 목적지 고객에게는 추천검색어를 " +
        "노출하지 않는 조건을 추가해야 합니다.",
    },
    {
      predictionId: "query_variety",
      actual: "-10%",
      verdict: "hit",
      note: "예측 -12%와 거의 같았습니다.",
    },
  ],

  nextActionId: "content_improvement",
  nextActionReason:
    "상담 인입이 예상만큼 줄지 않은 이유가 상세페이지 이탈로 확인됐습니다. " +
    "결정 지점에 기간 선택 가이드를 넣는 액션이 이어서 필요합니다.",
};

export const ACTION_PLANS: Record<string, ActionPlan> = {
  search_keyword: SEARCH_KEYWORD_PLAN,
};

/**
 * 관찰 화면에 그릴 지표.
 * `better` 는 값이 커지는 게 좋은지 작아지는 게 좋은지다. 진행률 계산에 쓴다.
 */
export const TIMELAPSE_SERIES = [
  { key: "conversion", label: "가입 전환율", unit: "%", better: "up" },
  { key: "research_days", label: "평균 탐색 기간", unit: "일", better: "down" },
  { key: "voc_transfer", label: "상담 인입", unit: "명", better: "down" },
] as const;
