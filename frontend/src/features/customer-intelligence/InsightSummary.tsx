import type {
  AgentMode,
  InsightReport,
  RunError,
  RunPhase,
  SourceId,
} from "./contracts";
import type { SubmissionErrorKind } from "./use-run-controller";

interface InsightSummaryProps {
  report: InsightReport | null;
  phase: RunPhase;
  agentMode: AgentMode | null;
  fallbackReason: string | null;
  submissionError: string | null;
  submissionErrorKind: SubmissionErrorKind | null;
  isCreating: boolean;
  error: RunError | null;
  onRetry: () => void;
}

const sourceLabels: Record<SourceId, string> = {
  search_history: "검색 이력",
  search_feedback: "검색 피드백",
  voc: "VOC",
};

const confidenceLabels = {
  high: "높은 신뢰",
  medium: "보통 신뢰",
  low: "낮은 신뢰",
};

function metricText(value: number | string, unit: string | null) {
  return `${typeof value === "number" ? value.toLocaleString("ko-KR") : value}${unit ?? ""}`;
}

function FailureState({
  unsupported,
  message,
  onRetry,
}: {
  unsupported: boolean;
  message: string;
  onRetry: () => void;
}) {
  return (
    <section className="panel insight-panel state-panel" aria-labelledby="failure-title">
      <div className="state-symbol state-symbol-error" aria-hidden="true">
        !
      </div>
      <p className="section-kicker">ANALYSIS PAUSED</p>
      <h2 id="failure-title">
        {unsupported
          ? "아직 이 질문은 지원하지 않아요"
          : "분석 서버에 연결하지 못했어요"}
      </h2>
      <p>{message}</p>
      {unsupported ? (
        <p className="support-range">
          현재는 <strong>검색 실패 후 고객센터 문의로 이어진 Journey</strong>를
          분석할 수 있습니다. 왼쪽 추천 질문으로 다시 시도해 보세요.
        </p>
      ) : (
        <button className="secondary-action" type="button" onClick={onRetry}>
          다시 분석
        </button>
      )}
    </section>
  );
}

export function InsightSummary({
  report,
  phase,
  agentMode,
  fallbackReason,
  submissionError,
  submissionErrorKind,
  isCreating,
  error,
  onRetry,
}: InsightSummaryProps) {
  if (submissionError && submissionErrorKind === "network") {
    return (
      <FailureState unsupported={false} message={submissionError} onRetry={onRetry} />
    );
  }

  if (phase === "failed") {
    return (
      <FailureState
        unsupported={error?.code === "unsupported_question"}
        message={error?.message ?? "분석을 완료하지 못했습니다."}
        onRetry={onRetry}
      />
    );
  }

  if (isCreating || phase === "running" || phase === "validating") {
    return (
      <section
        className="panel insight-panel loading-panel"
        aria-labelledby="loading-title"
        aria-busy="true"
      >
        <p className="section-kicker">LIVE ANALYSIS</p>
        <h2 id="loading-title">
          {phase === "validating"
            ? "근거와 수치를 검증하고 있어요"
            : "고객 Journey를 연결하고 있어요"}
        </h2>
        <p>
          {phase === "validating"
            ? "각 Insight가 실제 Tool 결과와 Evidence에 연결되는지 확인합니다."
            : "선택한 Source에서 동일 Topic의 행동 흐름을 찾고 있습니다."}
        </p>
        <div className="skeleton-stack" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
      </section>
    );
  }

  if (!report) {
    return (
      <section className="panel insight-panel welcome-panel" aria-labelledby="welcome-title">
        <div className="welcome-orbit" aria-hidden="true">
          <span>01</span>
          <span>02</span>
          <span>03</span>
        </div>
        <p className="section-kicker">CUSTOMER SIGNAL LAB</p>
        <h2 id="welcome-title">흩어진 행동을 하나의 고객 맥락으로</h2>
        <p>
          검색 이력, 피드백, VOC를 시간순으로 연결해 “몇 명인가”에서 끝나지
          않고 왜 그런지까지 근거와 함께 확인합니다.
        </p>
        <dl className="pattern-legend">
          <div>
            <dt>01</dt>
            <dd>검색 실패</dd>
          </div>
          <div>
            <dt>02</dt>
            <dd>24시간 내 재검색</dd>
          </div>
          <div>
            <dt>03</dt>
            <dd>72시간 내 VOC</dd>
          </div>
        </dl>
      </section>
    );
  }

  const primaryMetric = report.metrics[0];
  const isZero =
    report.ranked_customers.length === 0 && Number(primaryMetric?.value ?? 0) === 0;
  const vocDisabled = !report.scope.enabled_sources.includes("voc");

  return (
    <section className="panel insight-panel result-panel" aria-labelledby="insight-title">
      <div className="result-topline">
        <div>
          <p className="section-kicker">03 · VERIFIED INSIGHT</p>
          <div className="mode-row">
            <span
              className={`mode-badge ${phase === "degraded" ? "mode-degraded" : ""}`}
            >
              {agentMode === "gemini" ? "Gemini Agent" : "Fixture Replay"}
            </span>
            <span className="verified-badge">근거 검증 완료</span>
          </div>
        </div>
        {primaryMetric ? (
          <div className="hero-metric" aria-label={primaryMetric.label}>
            <strong>{metricText(primaryMetric.value, primaryMetric.unit)}</strong>
            <small>{primaryMetric.label}</small>
          </div>
        ) : null}
      </div>

      <h2 id="insight-title">{report.headline}</h2>
      <p className="executive-summary">{report.executive_summary}</p>

      <div className="source-strip" aria-label="결과에 사용한 Source">
        {report.sources_used.map((source) => (
          <span key={source}>{sourceLabels[source]}</span>
        ))}
      </div>

      {phase === "degraded" ? (
        <div className="degraded-callout">
          <strong>Gemini 대신 결정론적 Fixture로 완료했습니다.</strong>
          <p>{fallbackReason ?? "Agent 실행을 안전한 Replay로 전환했습니다."}</p>
        </div>
      ) : null}

      {isZero ? (
        <div className="zero-callout">
          <span aria-hidden="true">0</span>
          <div>
            <strong>완전한 패턴 일치 고객이 없습니다.</strong>
            <p>
              {vocDisabled
                ? "VOC Source를 켜고 다시 분석하면 검색 이후 문의 전환까지 확인할 수 있어요."
                : "기간이나 Source 범위를 조정해 다시 분석해 보세요."}
            </p>
          </div>
        </div>
      ) : null}

      {report.findings.length ? (
        <div className="insight-block">
          <h3>핵심 발견</h3>
          <div className="finding-grid">
            {report.findings.map((finding) => (
              <article key={`${finding.title}-${finding.description}`}>
                <span>{confidenceLabels[finding.confidence]}</span>
                <h4>{finding.title}</h4>
                <p>{finding.description}</p>
              </article>
            ))}
          </div>
        </div>
      ) : null}

      {report.signal_contributions.length ? (
        <div className="insight-block">
          <div className="block-heading">
            <h3>대표 고객 신호 구성</h3>
            <small>첫 번째 패턴 일치 고객 기준</small>
          </div>
          <div className="contribution-list">
            {report.signal_contributions.map((contribution) => (
              <div className="contribution-row" key={contribution.source_id}>
                <div>
                  <strong>{sourceLabels[contribution.source_id]}</strong>
                  <span>
                    {contribution.signals.map((signal) => signal.label).join(" · ")}
                  </span>
                </div>
                <b>+{contribution.score}</b>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {report.recommendations.length ? (
        <div className="insight-block">
          <h3>추천 Action</h3>
          <div className="recommendation-list">
            {report.recommendations.map((recommendation) => (
              <article key={recommendation.action_id}>
                <span aria-hidden="true">↗</span>
                <div>
                  <h4>{recommendation.title}</h4>
                  <p>{recommendation.reason}</p>
                </div>
              </article>
            ))}
          </div>
        </div>
      ) : null}

      {report.limitations.length ? (
        <div className="limitations-block">
          <h3>분석 한계</h3>
          <ul>
            {report.limitations.map((limitation) => (
              <li key={limitation}>{limitation}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
