import type {
  AnalysisMetricFact,
  AnalysisSignal,
  EvidenceRecord,
  JourneyEvent,
  SourceId,
} from "../../customer-intelligence/contracts";

/** 한 화면씩 넘어가는 셸의 상태. Next 라우트가 아니라 phase로 전환한다. */
export type CatchPhase = "ask" | "catching" | "result" | "trace";

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
