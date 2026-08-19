import type { CustomerJourneyResult, SourceId } from "./contracts";
import type { DetailState } from "./use-run-controller";

interface JourneyTimelineProps {
  customerId: string | null;
  state: DetailState<CustomerJourneyResult>;
  onOpenEvidence: (evidenceId: string, opener: HTMLElement) => void;
  onRetry: () => void;
}

const sourceLabels: Record<SourceId, string> = {
  search_history: "검색 이력",
  search_feedback: "검색 피드백",
  digital_behavior: "디지털 행동",
  subscription: "가입 정보",
  voc: "VOC",
};

const seoulTime = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul",
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

export function JourneyTimeline({
  customerId,
  state,
  onOpenEvidence,
  onRetry,
}: JourneyTimelineProps) {
  const events =
    state.status === "success" || state.status === "empty"
      ? [...state.data.events].sort(
          (left, right) =>
            Date.parse(left.occurred_at) - Date.parse(right.occurred_at) ||
            left.event_id.localeCompare(right.event_id),
        )
      : [];

  return (
    <section className="panel journey-panel" aria-labelledby="journey-title">
      <div className="panel-heading">
        <div>
          <p className="section-kicker">05 · JOURNEY</p>
          <h2 id="journey-title">고객 Journey</h2>
        </div>
        {customerId ? <span className="customer-chip">{customerId}</span> : null}
      </div>

      {!customerId || state.status === "idle" ? (
        <div className="panel-placeholder">
          <span aria-hidden="true">↳</span>
          <p>패턴 일치 고객을 선택하면 채널 통합 Journey를 보여드려요.</p>
        </div>
      ) : state.status === "loading" ? (
        <div className="detail-loading" aria-busy="true">
          <span className="loading-ring" aria-hidden="true" />
          <p>{customerId} Journey를 시간순으로 연결하고 있어요.</p>
        </div>
      ) : state.status === "error" ? (
        <div className="detail-error" role="alert">
          <strong>Journey를 표시하지 못했습니다.</strong>
          <p>{state.error}</p>
          <button className="secondary-action" type="button" onClick={onRetry}>
            Journey 다시 불러오기
          </button>
        </div>
      ) : events.length === 0 ? (
        <div className="panel-placeholder zero-placeholder">
          <span aria-hidden="true">0</span>
          <p>선택한 고객의 분석 기간 내 이벤트가 없습니다.</p>
        </div>
      ) : (
        <ol className="journey-list" aria-label={`${customerId} 고객 Journey`}>
          {events.map((journeyEvent, index) => (
            <li className={`journey-event source-${journeyEvent.source_id}`} key={journeyEvent.event_id}>
              <div className="journey-rail" aria-hidden="true">
                <span>{String(index + 1).padStart(2, "0")}</span>
              </div>
              <article>
                <div className="event-meta">
                  <span className="event-source">
                    {sourceLabels[journeyEvent.source_id]}
                  </span>
                  <time dateTime={journeyEvent.occurred_at}>
                    {seoulTime.format(new Date(journeyEvent.occurred_at))}
                  </time>
                </div>
                <h3>{journeyEvent.action}</h3>
                <p className="event-topic">
                  {journeyEvent.topic} <span>·</span> {journeyEvent.outcome}
                </p>
                <p className="event-copy">{journeyEvent.text}</p>
                <button
                  className="evidence-action"
                  type="button"
                  aria-label={`${journeyEvent.evidence_id} 근거 보기`}
                  onClick={(event) =>
                    onOpenEvidence(
                      journeyEvent.evidence_id,
                      event.currentTarget,
                    )
                  }
                >
                  근거 보기 <span aria-hidden="true">↗</span>
                </button>
              </article>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
