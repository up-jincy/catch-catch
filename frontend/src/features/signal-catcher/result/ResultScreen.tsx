"use client";

import { useState } from "react";

import type { CatchAction, CatchReport, DepthMode, RunOutcome } from "../state/types";

import { ActionPreview } from "./ActionPreview";
import { EvidencePanel } from "./EvidencePanel";
import { JourneyFlow } from "./JourneyFlow";
import styles from "./result.module.css";

type Tab = "journey" | "insight" | "log";

interface ResultScreenProps {
  report: CatchReport;
  outcome: RunOutcome;
  depth: DepthMode;
  onDepthChange: (depth: DepthMode) => void;
  onOpenTrace: () => void;
  onRestart: () => void;
  /** 액션 상세로 이동. 액션 탭 카드가 곧 실험 목록 역할을 한다. */
  onOpenAction: (actionId: string) => void;
  experimentOf: (actionId: string) => { status: string; elapsedDays: number; observeDays: number; hits: number; total: number } | undefined;
}

export function ResultScreen({
  report,
  outcome,
  depth,
  onDepthChange,
  onOpenTrace,
  onRestart,
  onOpenAction,
  experimentOf,
}: ResultScreenProps) {
  const [tab, setTab] = useState<Tab>("journey");
  const [evidenceId, setEvidenceId] = useState<string | null>(null);
  const [action, setAction] = useState<CatchAction | null>(null);
  const [limitsOpen, setLimitsOpen] = useState(false);

  const degraded = outcome === "degraded";
  const findings = depth === "analyst"
    ? report.findings
    : report.findings.filter((claim) => claim.verdict === "passed");

  const tabs: { key: Tab; label: string }[] = [
    { key: "journey", label: "시그널 저니" },
    { key: "insight", label: "인사이트 & 액션" },
    ...(depth === "analyst" ? [{ key: "log" as const, label: "분석 로그" }] : []),
  ];

  return (
    <div className={styles.screen}>
      <div className={styles.inner}>
        <header className={styles.head}>
          <div className={styles.headTop}>
            {degraded ? (
              <button
                type="button"
                className={styles.degraded}
                onClick={() => setLimitsOpen((prev) => !prev)}
                aria-expanded={limitsOpen}
              >
                부분 캐치
              </button>
            ) : null}
            <span className={styles.headSpacer} />
            <div className={styles.depth} role="group" aria-label="정보 밀도">
              {(["basic", "analyst"] as const).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  data-on={depth === mode}
                  onClick={() => onDepthChange(mode)}
                >
                  {mode === "basic" ? "기본" : "분석가"}
                </button>
              ))}
            </div>
          </div>

          <h1 className={styles.headline}>
            {report.headline}
            <br />
            <em>{report.headlineCount.toLocaleString("ko-KR")}명</em>을 찾았어요
          </h1>
          {report.headlineTrailer ? (
            <p className={styles.trailer}>{report.headlineTrailer}</p>
          ) : null}

          {limitsOpen ? (
            <ul className={styles.limits}>
              {report.limitations.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          ) : null}

          <p className={styles.summary}>{report.summary}</p>

          <dl className={styles.metrics}>
            {report.metrics.map((metric) => {
              const missing = Number.isNaN(metric.value);
              return (
                <div key={metric.metric_key} data-missing={missing}>
                  <dt>{metric.label}</dt>
                  <dd>
                    {missing ? (
                      <span className={styles.metricMissing}>근거 부족</span>
                    ) : (
                      <>
                        {metric.value.toLocaleString("ko-KR")}
                        <small>{metric.unit}</small>
                      </>
                    )}
                  </dd>
                  {depth === "analyst" ? (
                    <p className={styles.metricKey}>{metric.metric_key}</p>
                  ) : null}
                </div>
              );
            })}
          </dl>

          <ul className={styles.signals} aria-label="시그널 기여도">
            {report.signals.map((signal) => (
              <li key={signal.signal_key}>
                <span className={styles.signalLabel}>{signal.label}</span>
                <span className={styles.signalBar}>
                  <i style={{ width: `${Math.round(signal.contribution * 100)}%` }} />
                </span>
                <span className={styles.signalValue}>
                  {Math.round(signal.contribution * 100)}%
                </span>
              </li>
            ))}
          </ul>
        </header>

        <nav className={styles.tabs} aria-label="분석 결과 보기">
          {tabs.map((item) => (
            <button
              key={item.key}
              type="button"
              data-on={tab === item.key}
              onClick={() => setTab(item.key)}
            >
              {item.label}
            </button>
          ))}
        </nav>

        <section className={styles.panel}>
          {tab === "journey" ? (
            <JourneyFlow report={report} depth={depth} onOpenEvidence={setEvidenceId} />
          ) : null}

          {tab === "insight" ? (
            <div className={styles.insight}>
              <div>
                <h3 className={styles.blockTitle}>검증된 발견</h3>
                <ul className={styles.findings}>
                  {findings.map((claim) => (
                    <li key={claim.claimId} data-verdict={claim.verdict}>
                      <p className={styles.findingText}>{claim.statement}</p>
                      {claim.verdict === "rejected" ? (
                        <p className={styles.findingReject}>{claim.rejectedReason}</p>
                      ) : (
                        <div className={styles.chips}>
                          {claim.evidenceIds.map((id) => (
                            <button key={id} type="button" onClick={() => setEvidenceId(id)}>
                              근거 {id}
                            </button>
                          ))}
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
              </div>

              <div>
                <h3 className={styles.blockTitle}>다음 행동</h3>
                <ul className={styles.actions}>
                  {report.actions.map((item) => (
                    <li key={item.actionId}>
                      {(() => {
                        const exp = experimentOf(item.actionId);
                        if (!exp) return null;
                        return (
                          <p className={styles.actionState} data-status={exp.status}>
                            <span>
                              {exp.status === "done"
                                ? `실험 완료 · 예측 ${exp.total}개 중 ${exp.hits}개 적중`
                                : `관찰 중 · ${exp.elapsedDays}일째`}
                            </span>
                            {exp.status === "watching" ? (
                              <i>
                                <b
                                  style={{
                                    width: `${(exp.elapsedDays / exp.observeDays) * 100}%`,
                                  }}
                                />
                              </i>
                            ) : null}
                          </p>
                        );
                      })()}
                      <h4>{item.title}</h4>
                      <p>{item.reason}</p>
                      <div className={styles.actionFoot}>
                        <div className={styles.chips}>
                          {item.evidenceIds.slice(0, 2).map((id) => (
                            <button key={id} type="button" onClick={() => setEvidenceId(id)}>
                              근거 {id}
                            </button>
                          ))}
                        </div>
                        {item.keywords ? (
                          <button
                            type="button"
                            className={styles.primaryBtn}
                            onClick={() => onOpenAction(item.actionId)}
                          >
                            {experimentOf(item.actionId) ? "경과 보기" : "미리보기"}
                          </button>
                        ) : (
                          <span className={styles.actionSoon}>다음 단계</span>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          ) : null}

          {tab === "log" ? (
            <ol className={styles.log}>
              {report.planSteps.map((step) => (
                <li key={step.stepId}>
                  <span className={styles.logId}>{step.stepId}</span>
                  <div>
                    <h4>{step.objective}</h4>
                    <p>
                      <code>{step.primitive}</code>
                      <span>{(step.durationMs / 1000).toFixed(1)}초</span>
                    </p>
                    {step.revisedFrom ? (
                      <p className={styles.logRevised}>계획 수정됨 — {step.revisedFrom}</p>
                    ) : null}
                  </div>
                </li>
              ))}
            </ol>
          ) : null}
        </section>

        <footer className={styles.foot}>
          <button type="button" className={styles.traceLink} onClick={onOpenTrace}>
            이 결론이 나온 과정 보기 <span aria-hidden="true">→</span>
          </button>
          <button type="button" className={styles.ghostBtn} onClick={onRestart}>
            새로 캐치하기
          </button>
        </footer>
      </div>

      {evidenceId ? (
        <EvidencePanel evidenceId={evidenceId} onClose={() => setEvidenceId(null)} />
      ) : null}

      {action ? (
        <ActionPreview
          action={action}
          segmentLabel={report.segmentLabel}
          onClose={() => setAction(null)}
        />
      ) : null}
    </div>
  );
}
