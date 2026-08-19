import type {
  RunPhase,
  RunStreamEvent,
  SourceId,
  ToolName,
} from "./contracts";

interface AgentTraceProps {
  events: RunStreamEvent[];
  phase: RunPhase;
  fallbackReason: string | null;
  isCreating?: boolean;
}

const toolLabels: Record<ToolName, string> = {
  catalog_sources: "Source 카탈로그 확인",
  aggregate_events: "이벤트 집계",
  match_journey_pattern: "Journey 패턴 매칭",
  rank_customers: "고객 위험 신호 정렬",
  get_customer_journey: "대표 고객 Journey 조회",
  get_evidence: "원본 Evidence 확인",
};

const sourceLabels: Record<SourceId, string> = {
  search_history: "검색 이력",
  search_feedback: "검색 피드백",
  voc: "VOC",
};

const phaseLabels: Record<RunPhase, string> = {
  idle: "대기 중",
  running: "분석 중",
  validating: "근거 검증 중",
  completed: "분석 완료",
  degraded: "제한 모드로 완료",
  failed: "분석 실패",
};

function sources(values: SourceId[]) {
  if (!values.length) return null;
  return values.map((source) => sourceLabels[source]).join(" · ");
}

function eventView(event: RunStreamEvent) {
  switch (event.type) {
    case "plan":
      return {
        title: "실행 계획 수립",
        detail: event.data.steps.join(" → "),
        state: "done",
      };
    case "tool_started":
      return {
        title: toolLabels[event.data.tool],
        detail: sources(event.data.source) ?? "분석 범위 확인 중",
        state: "active",
      };
    case "tool_completed":
      return {
        title: toolLabels[event.data.tool],
        detail: `${sources(event.data.source) ?? "전체 Source"} · ${event.data.count.toLocaleString("ko-KR")}건 · ${Math.round(event.data.duration_ms)}ms`,
        state: "done",
      };
    case "validating":
      return {
        title: "수치와 Evidence 연결 검증",
        detail: "Tool 결과와 최종 Insight의 근거를 대조합니다.",
        state: "active",
      };
    case "fallback":
      return {
        title: "Fixture Replay 전환",
        detail:
          event.data.reason ??
          event.data.message ??
          "Gemini 분석을 결정론적 Replay로 전환했습니다.",
        state: "warning",
      };
    case "result":
      return {
        title: "검증된 Insight 구성",
        detail: `${event.data.report.ranked_customers.length}명의 패턴 일치 고객을 구성했습니다.`,
        state: "done",
      };
    case "error":
      return { title: "분석 중단", detail: event.data.message, state: "error" };
    case "done":
      return {
        title: event.data.status === "completed" ? "Run 완료" : "Run 실패",
        detail:
          event.data.status === "completed"
            ? "공개 가능한 결과만 화면에 전달했습니다."
            : "결과를 만들지 못했습니다.",
        state: event.data.status === "completed" ? "done" : "error",
      };
  }
}

function latestStatus(
  phase: RunPhase,
  events: RunStreamEvent[],
  isCreating: boolean,
) {
  if (isCreating) return "새 분석 Run을 준비하고 있습니다.";
  const latest = events.at(-1);
  if (!latest) {
    return phase === "idle"
      ? "질문을 선택하면 분석 과정이 여기에 표시됩니다."
      : phase === "running"
        ? "Agent가 분석을 시작했습니다."
        : phaseLabels[phase];
  }
  const view = eventView(latest);
  if (latest.type === "tool_started") {
    return `${view.title} 시작 · ${view.detail}`;
  }
  if (latest.type === "tool_completed") {
    return `${view.title} 완료 · ${latest.data.count.toLocaleString("ko-KR")}건 · ${Math.round(latest.data.duration_ms)}ms`;
  }
  return view.title;
}

export function AgentTrace({
  events,
  phase,
  fallbackReason,
  isCreating = false,
}: AgentTraceProps) {
  const displayPhase = isCreating ? "running" : phase;

  return (
    <section className="panel trace-panel" aria-labelledby="trace-title">
      <div className="panel-heading trace-heading">
        <div>
          <p className="section-kicker">02 · TRACE</p>
          <h2 id="trace-title">Agent 진행 상황</h2>
        </div>
        <span className={`phase-badge phase-${displayPhase}`}>
          <span className="phase-dot" aria-hidden="true" />
          {phaseLabels[displayPhase]}
        </span>
      </div>

      <p
        className="sr-only"
        role="status"
        aria-live="polite"
        aria-atomic="true"
      >
        {latestStatus(displayPhase, events, isCreating)}
      </p>

      {events.length ? (
        <ol className="trace-list" aria-label="공개 Agent 실행 기록">
          {events.map((event) => {
            const view = eventView(event);
            return (
              <li className={`trace-item trace-${view.state}`} key={event.id}>
                <span className="trace-marker" aria-hidden="true" />
                <div>
                  <strong>{view.title}</strong>
                  <p>{view.detail}</p>
                </div>
              </li>
            );
          })}
        </ol>
      ) : (
        <div className="trace-empty">
          <span aria-hidden="true">⌁</span>
          <p>
            실행하면 계획, 조회 Source, 처리 건수와 검증 상태를 실시간으로
            보여드려요.
          </p>
        </div>
      )}

      {fallbackReason ? (
        <p className="trace-notice">
          <span aria-hidden="true">!</span>
          {fallbackReason}
        </p>
      ) : null}
    </section>
  );
}
