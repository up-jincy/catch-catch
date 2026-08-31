import type {
  AnalysisMetricFact,
  AnalysisSignal,
  EvidenceRecord,
  JourneyEvent,
  SourceId,
} from "../../customer-intelligence/contracts";

/** 한 화면씩 넘어가는 셸의 상태. Next 라우트가 아니라 phase로 전환한다. */
export type CatchPhase = "ask" | "catching" | "result" | "trace" | "action";

/** 결과 화면의 정보 밀도. 페이지를 나누지 않고 같은 화면의 노출량을 바꾼다. */
export type DepthMode = "basic" | "analyst";

export type StageKey =
  | "goal"
  | "plan"
  | "analyze"
  | "insight"
  | "verify";

export type StageStatus = "pending" | "active" | "done";

/**
 * 로딩 화면 스테퍼 한 칸. Backend Canonical Run Event 하나에 대응하며
 * `detail`은 그 이벤트가 실제로 실어 온 문장을 그대로 노출한다.
 */
export interface Stage {
  key: StageKey;
  label: string;
  /** 심박 아래 스테퍼에 붙는 짧은 이름. */
  short: string;
  event: string;
  detail: string | null;
  status: StageStatus;
}

/** 저니 노드 하나. `JourneyEvent`에 레인 좌표와 강도를 얹은 표현용 값. */
export interface JourneyNode extends JourneyEvent {
  lane: SourceId;
  /** 시간순 위치. 열 하나에 이벤트 하나를 놓아 항상 왼쪽에서 오른쪽으로 흐른다. */
  column: number;
  intensity: number;
  tone: "signal" | "negative" | "repeat";
  insight: string;
}

export interface JourneyLane {
  id: SourceId;
  label: string;
}

/** 검증 기록 페이지의 주장 하나. 탈락한 주장도 같은 모양으로 싣는다. */
export interface LedgerClaim {
  claimId: string;
  statement: string;
  verdict: "passed" | "rejected";
  rejectedReason: string | null;
  chain: {
    claim: string;
    fact: string;
    source: string;
    evidence: string;
  };
  evidenceIds: string[];
}

export interface TraceScore {
  claimsPassed: number;
  claimsTotal: number;
  evidenceCoverage: number;
  steps: number;
  sources: number;
  planRevisions: number;
  durationMs: number;
}

export interface PlanStep {
  stepId: string;
  primitive: string;
  objective: string;
  durationMs: number;
  revisedFrom: string | null;
}

export interface KeywordSuggestion {
  keyword: string;
  reason: string;
}

export interface CatchAction {
  actionId: string;
  title: string;
  reason: string;
  evidenceIds: string[];
  /** 신청서 MVP 범위의 Mock Interaction. 이 액션만 실행 화면을 갖는다. */
  keywords: KeywordSuggestion[] | null;
}

export interface CatchReport {
  headline: string;
  headlineCount: number;
  headlineTrailer: string | null;
  summary: string;
  segmentLabel: string;
  metrics: AnalysisMetricFact[];
  signals: AnalysisSignal[];
  journey: JourneyNode[];
  lanes: JourneyLane[];
  findings: LedgerClaim[];
  actions: CatchAction[];
  limitations: string[];
  planSteps: PlanStep[];
  score: TraceScore;
  datasetVersion: string;
  adapterVersions: Record<SourceId, string>;
}

/** 액션 적용 전후를 비교할 시안 한 장. */
export interface MockupItem {
  kind: "query" | "suggestion" | "result" | "guide";
  text: string;
  sub: string | null;
  /** TO-BE 에서 새로 생기는 항목. 매직 연출로 하나씩 그려진다. */
  added: boolean;
}

export interface ActionMockup {
  label: string;
  context: string;
  items: MockupItem[];
}

/**
 * 적용 시 예상되는 변화 하나.
 * 좋아지는 것과 나빠질 수 있는 것을 같은 모양으로 싣는다.
 * 좋은 것만 실으면 검증하지 않은 제안과 구분되지 않는다.
 */
export interface Prediction {
  predictionId: string;
  direction: "gain" | "risk";
  label: string;
  from: string;
  to: string;
  delta: string;
  reason: string;
  evidenceIds: string[];
}

/** 관찰 기간이 끝난 뒤의 실측과 채점. 빗나간 예측도 그대로 싣는다. */
export interface PredictionOutcome {
  predictionId: string;
  actual: string;
  verdict: "hit" | "miss";
  note: string;
}

/** 타임랩스 한 프레임. */
export interface TimelapsePoint {
  day: number;
  date: string;
  values: Record<string, number>;
}

export interface ActionPlan {
  actionId: string;
  title: string;
  /** 실험 겹침 판정 키. 같은 Segment 를 건드리면 효과를 귀인할 수 없다. */
  segmentLabel: string;
  segmentSize: number;
  applyLabel: string;
  asIs: ActionMockup;
  toBe: ActionMockup;
  predictions: Prediction[];
  observeDays: number;
  timelapse: TimelapsePoint[];
  outcomes: PredictionOutcome[];
  /** 채점 결과가 정당화하는 다음 액션. */
  nextActionId: string | null;
  nextActionReason: string | null;
}

/** 액션 페이지의 4단계. */
export type ActionStage = "preview" | "applying" | "watching" | "report";

/**
 * 적용한 실험. Run 하나보다 오래 살기 때문에 CatchSession 밖에 둔다.
 * localStorage 에 보관해 첫 화면으로 나갔다 와도, 새로고침해도 이어서 볼 수 있다.
 */
export interface Experiment {
  actionId: string;
  title: string;
  segmentLabel: string;
  observeDays: number;
  elapsedDays: number;
  status: "watching" | "done";
  startedAt: string;
  hits: number;
  total: number;
}

export type RunOutcome = "completed" | "degraded" | "failed";

export interface ClarificationPrompt {
  clarificationId: string;
  question: string;
  hint: string;
}

/** 화면이 소비하는 단일 상태. Mock과 실제 Run Controller가 같은 모양을 만든다. */
export interface CatchSession {
  phase: CatchPhase;
  question: string;
  stages: Stage[];
  activeStage: StageKey | null;
  clarification: ClarificationPrompt | null;
  outcome: RunOutcome | null;
  report: CatchReport | null;
  failureReason: string | null;
  suggestedQuestions: string[];
}

export interface SourceOption {
  id: SourceId;
  label: string;
  note: string;
  topics: string[];
  interval: string;
}

export type EvidenceMap = Record<string, EvidenceRecord>;
