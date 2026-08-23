import type {
  AnyRunStreamEvent,
  RunPhase,
  SourceId,
  ToolName,
} from "./contracts";
import { primitiveLabel } from "./primitive-catalog";
import { selectVisibleRunEvents } from "./run-selectors";
import { sourceLabel } from "./source-catalog";

interface AgentTraceProps {
  events: AnyRunStreamEvent[];
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
  digital_behavior: "디지털 행동",
  subscription: "가입 정보",
  voc: "VOC",
};

const phaseLabels: Record<RunPhase, string> = {
  idle: "대기 중",
  running: "분석 중",
  validating: "근거 검증 중",
  completed: "분석 완료",
  degraded: "제한 모드로 완료",
  failed: "분석 실패",
  awaiting_clarification: "답변 대기",
};

function sources(values: SourceId[]) {
  if (!values.length) return null;
  return values.map((source) => sourceLabels[source] ?? sourceLabel(source)).join(" · ");
}

function eventView(event: AnyRunStreamEvent) {
  switch (event.type) {
    case "run_started":
      return {
        title: "분석을 시작합니다",
        detail: "질문과 분석 범위를 확인하고 있습니다.",
        state: "active",
      };
    case "goal_created":
      return {
        title: "분석 목표를 세웠습니다",
        detail: event.data.goal.objective,
        state: "done",
      };
    case "clarification_required":
      return {
        title: "먼저 확인이 필요합니다",
        detail: event.data.question,
        state: "warning",
      };
    case "plan_created":
      return {
        title: `계획을 세웠습니다 — ${event.data.plan.steps.length}개 단계로 진행하겠습니다`,
        detail: event.data.plan.rationale,
        state: "done",
      };
    case "plan_revised":
      return {
        title: `확인된 결과에 맞춰 계획을 수정했습니다 (revision ${event.data.plan.revision})`,
        detail: event.data.plan.rationale,
        state: "done",
      };
    case "step_started":
      return {
        title: `이제 ${primitiveLabel(event.data.primitive)} 단계를 실행합니다`,
        detail:
          event.data.selection_reason ??
          event.data.objective ??
          `${event.data.step_id} 실행 중`,
        state: "active",
      };
    case "fact_created": {
      const metric = event.data.fact.metrics[0];
      return {
        title: `${primitiveLabel(event.data.fact.primitive)} 결과를 받았습니다`,
        detail: metric
          ? `${metric.label} = ${metric.value}${metric.unit ? ` ${metric.unit}` : ""} — 결과를 검증해 Fact로 고정했습니다.`
          : `${event.data.fact.metrics.length}개 검증 Metric을 고정했습니다.`,
        state: "done",
      };
    }
    case "analysis_note_created": {
      const claim = event.data.note.claims[0];
      const observed = claim ? `확인: ${claim.rendered_text}` : event.data.note.objective;
      return {
        title: "단계 결과를 검증했습니다",
        detail: `${observed} → 다음으로 ${event.data.note.next_action}`,
        state: "done",
      };
    }
    case "step_completed":
      return {
        title:
          event.data.status === "completed"
            ? "단계를 마쳤습니다"
            : "단계를 종료했습니다",
        detail: `${event.data.step_id} · ${Math.round(event.data.duration_ms)}ms · Result ${event.data.result_ids.length}개`,
        state: event.data.status === "failed" ? "error" : "done",
      };
    case "report_validating":
      return {
        title: "최종 보고서를 검증하고 있습니다",
        detail: `${event.data.result_ids.length || event.data.fact_ids.length}개 실행 근거와 결론을 대조합니다.`,
        state: "active",
      };
    case "plan":
      return {
        title: "실행 계획 수립",
        detail: event.data.steps.join(" → "),
        state: "done",
      };
    case "tool_started":
      return {
        title: toolLabels[event.data.tool] ?? event.data.tool,
        detail: sources(event.data.source) ?? "분석 범위 확인 중",
        state: "active",
      };
    case "tool_completed":
      return {
        title: toolLabels[event.data.tool] ?? event.data.tool,
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
        title: "결론을 정리했습니다",
        detail: event.data.report.headline,
        state: "done",
      };
    case "error":
      return { title: "분석을 중단했습니다", detail: event.data.message, state: "error" };
    case "done":
      return {
        title:
          event.data.status === "completed"
            ? "분석을 마쳤습니다"
            : event.data.status === "degraded"
              ? "제한 조건으로 기록을 마쳤습니다"
              : "분석에 실패했습니다",
        detail:
          event.data.status === "completed"
            ? "검증된 결과만 화면과 문서에 공개했습니다."
            : "결과를 만들지 못했습니다.",
        state: event.data.status === "completed" ? "done" : "error",
      };
  }
}

function latestStatus(
  phase: RunPhase,
  events: AnyRunStreamEvent[],
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
  const visibleEvents = selectVisibleRunEvents(events);

  return (
    <section className="panel trace-panel" aria-labelledby="trace-title">
      <div className="panel-heading trace-heading">
        <div>
          <p className="section-kicker">03 · TRACE</p>
          <h2 id="trace-title">Agent 진행 상황</h2>
        </div>
        <span className={`phase-badge phase-${displayPhase}`}>
          <span className="phase-dot" aria-hidden="true" />
          {phaseLabels[displayPhase] ?? displayPhase}
        </span>
      </div>

      <p
        className="sr-only"
        role="status"
        aria-live="polite"
        aria-atomic="true"
      >
        {latestStatus(displayPhase, visibleEvents, isCreating)}
      </p>

      {visibleEvents.length ? (
        <ol className="trace-list" aria-label="공개 Agent 실행 기록">
          {visibleEvents.map((event) => {
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
