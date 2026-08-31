"use client";

import type { CatchReport } from "../state/types";

import styles from "./trace.module.css";

interface TraceScreenProps {
  report: CatchReport;
  question: string;
  onBack: () => void;
}

/**
 * Tier 3. 결론까지의 계보를 끝까지 파고드는 화면.
 * 통과한 주장뿐 아니라 탈락한 주장과 그 사유까지 같은 무게로 싣는다.
 */
export function TraceScreen({ report, question, onBack }: TraceScreenProps) {
  const { score } = report;
  const passRate = Math.round((score.claimsPassed / score.claimsTotal) * 100);

  return (
    <div className={styles.screen}>
      <div className={styles.inner}>
        <button type="button" className={styles.back} onClick={onBack}>
          <span aria-hidden="true">←</span> 결과로 돌아가기
        </button>

        <header className={styles.head}>
          <p className={styles.tag}>검증 기록</p>
          <h1>이 결론은 이렇게 나왔어요</h1>
          <p className={styles.question}>&ldquo;{question}&rdquo;</p>
        </header>

        <dl className={styles.score}>
          <div data-tone={passRate === 100 ? "ok" : "warn"}>
            <dt>검증 통과 주장</dt>
            <dd>
              {score.claimsPassed}
              <small>/{score.claimsTotal}</small>
            </dd>
          </div>
          <div data-tone={score.evidenceCoverage === 100 ? "ok" : "warn"}>
            <dt>근거 커버리지</dt>
            <dd>
              {score.evidenceCoverage}
              <small>%</small>
            </dd>
          </div>
          <div>
            <dt>분석 스텝</dt>
            <dd>{score.steps}</dd>
          </div>
          <div>
            <dt>사용 소스</dt>
            <dd>{score.sources}</dd>
          </div>
          <div data-tone={score.planRevisions > 0 ? "warn" : undefined}>
            <dt>계획 수정</dt>
            <dd>
              {score.planRevisions}
              <small>회</small>
            </dd>
          </div>
          <div>
            <dt>소요</dt>
            <dd>
              {(score.durationMs / 1000).toFixed(1)}
              <small>초</small>
            </dd>
          </div>
        </dl>

        {score.planRevisions > 0 ? (
          <p className={styles.revision}>
            Agent가 분석 도중 계획을 {score.planRevisions}회 수정했습니다.
            가입 상태를 포함하지 않은 첫 계획으로는 &lsquo;가입 직전 이탈&rsquo;을 구분할 수 없었습니다.
          </p>
        ) : null}

        <section>
          <h2 className={styles.blockTitle}>주장별 근거 계보</h2>
          <p className={styles.blockNote}>
            문장 하나에서 원본 데이터 한 건까지 참조가 끊기지 않는지 확인할 수 있어요.
          </p>

          <ul className={styles.ledger}>
            {report.findings.map((claim) => (
              <li key={claim.claimId} data-verdict={claim.verdict}>
                <p className={styles.statement}>
                  {claim.statement}
                  <span className={styles.verdict}>
                    {claim.verdict === "passed" ? "검증 통과" : "근거 부족 · 제외"}
                  </span>
                </p>

                {claim.rejectedReason ? (
                  <p className={styles.reject}>{claim.rejectedReason}</p>
                ) : null}

                <ul className={styles.chain}>
                  <li>
                    <span>CLAIM</span>
                    <span>{claim.chain.claim}</span>
                  </li>
                  <li>
                    <span>FACT</span>
                    <span>{claim.chain.fact}</span>
                  </li>
                  <li>
                    <span>SOURCE</span>
                    <span>{claim.chain.source}</span>
                  </li>
                  <li>
                    <span>EVIDENCE</span>
                    <span>{claim.chain.evidence}</span>
                  </li>
                </ul>
              </li>
            ))}
          </ul>
        </section>

        <section>
          <h2 className={styles.blockTitle}>재현 정보</h2>
          <p className={styles.blockNote}>
            같은 데이터 버전으로 다시 실행하면 같은 결과가 나와야 합니다.
          </p>
          <dl className={styles.provenance}>
            <div>
              <dt>dataset_version</dt>
              <dd>{report.datasetVersion}</dd>
            </div>
            {Object.entries(report.adapterVersions).map(([source, version]) => (
              <div key={source}>
                <dt>{source}</dt>
                <dd>adapter {version}</dd>
              </div>
            ))}
          </dl>
        </section>

        {report.limitations.length ? (
          <section>
            <h2 className={styles.blockTitle}>확인하지 못한 것</h2>
            <ul className={styles.limits}>
              {report.limitations.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </section>
        ) : null}
      </div>
    </div>
  );
}
