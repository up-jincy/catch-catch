import type { ArtifactSummary } from "./contracts";

export type LoadStatus = "idle" | "loading" | "success" | "error";

interface RunHistoryProps {
  items: ArtifactSummary[];
  status: LoadStatus;
  selectedRunId: string | null;
  onSelect: (runId: string) => void;
}

const statusLabels = {
  queued: "대기",
  running: "진행 중",
  awaiting_clarification: "답변 대기",
  completed: "완료",
  degraded: "제한 완료",
  failed: "실패",
};

const dateFormatter = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul",
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

export function RunHistory({
  items,
  status,
  selectedRunId,
  onSelect,
}: RunHistoryProps) {
  return (
    <section className="panel history-panel" aria-labelledby="history-title">
      <div className="panel-heading">
        <div>
          <p className="section-kicker">04 · HISTORY</p>
          <h2 id="history-title">이전 Run</h2>
        </div>
        {items.length ? <span className="count-chip">{items.length}</span> : null}
      </div>

      {status === "loading" || status === "idle" ? (
        <p className="history-state" aria-busy="true">Run 기록을 불러오는 중입니다.</p>
      ) : status === "error" ? (
        <p className="history-state" role="alert">Run 기록을 불러오지 못했습니다.</p>
      ) : items.length === 0 ? (
        <p className="history-state">아직 저장된 Run이 없습니다.</p>
      ) : (
        <ol className="history-list">
          {items.map((item) => (
            <li key={item.run_id}>
              <button
                type="button"
                className="history-item"
                aria-current={item.run_id === selectedRunId ? "true" : undefined}
                onClick={() => onSelect(item.run_id)}
              >
                <span className={`history-status status-${item.status}`}>
                  {statusLabels[item.status]}
                </span>
                <strong>{item.headline}</strong>
                <small>
                  {dateFormatter.format(new Date(item.updated_at))} · {item.question}
                </small>
              </button>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
