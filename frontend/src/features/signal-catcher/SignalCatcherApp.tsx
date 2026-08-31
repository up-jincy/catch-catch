"use client";

import { useEffect, useRef, useState } from "react";

import { ActionScreen } from "./action/ActionScreen";
import { ExperimentMenu } from "./action/ExperimentMenu";
import { AskScreen } from "./ask/AskScreen";
import { SignalMark } from "./brand/Brand";
import { CatchingScreen } from "./catching/CatchingScreen";
import { ResultScreen } from "./result/ResultScreen";
import { useCatchSession, useDemoOptions } from "./state/use-catch-session";
import { ACTION_PLANS } from "./state/action-mock";
import { DEMO_QUESTION } from "./state/mock";
import { useExperiments } from "./state/use-experiments";
import { OVERLAY_ID } from "./Overlay";
import type { DepthMode } from "./state/types";
import { TraceScreen } from "./trace/TraceScreen";

import styles from "./catcher.module.css";

const FONT_HREF =
  "https://fonts.googleapis.com/css2?family=Gasoek+One&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans+KR:wght@400;500;600;700&display=swap";

/**
 * 화면 상태를 URL 에 반영한다. 라우트를 쪼개면 전환 연출이 끊기기 때문에
 * 한 페이지를 유지한 채 history 만 동기화한다.
 * 새로고침, 뒤로가기, 링크 공유가 모두 살아난다.
 */
function syncUrl(phase: string, push: boolean) {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  if (phase === "result" || phase === "trace" || phase === "action") {
    url.searchParams.set("view", phase);
  }
  else url.searchParams.delete("view");
  const next = `${url.pathname}${url.search}`;
  if (next === `${window.location.pathname}${window.location.search}`) return;
  if (push) window.history.pushState({ phase }, "", next);
  else window.history.replaceState({ phase }, "", next);
}

export function SignalCatcherApp() {
  const options = useDemoOptions();
  const controller = useCatchSession(options);
  const { session } = controller;

  const experiments = useExperiments();
  const [question, setQuestion] = useState("");
  const [depth, setDepth] = useState<DepthMode>("basic");
  const [actionId, setActionId] = useState<string>("search_keyword");
  const lastPhase = useRef(session.phase);

  // 로딩(catching)은 되돌아갈 지점이 아니라서 기록에 남기지 않는다.
  useEffect(() => {
    if (options.pause) return;
    if (lastPhase.current === session.phase) return;
    lastPhase.current = session.phase;
    if (session.phase === "catching") return;
    syncUrl(session.phase, true);
  }, [session.phase, options.pause]);

  useEffect(() => {
    if (options.pause) return;
    function onPop() {
      const view = new URL(window.location.href).searchParams.get("view");
      if (view === "result" || view === "trace" || view === "action") {
        controller.restore(view, session.question || DEMO_QUESTION);
      } else {
        controller.reset();
      }
    }
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [controller, session.question, options.pause]);

  // 자동 진입이나 새로고침 복원으로 들어와도 입력창에 질문이 남아 있어야 한다.
  useEffect(() => {
    if (session.question && !question) setQuestion(session.question);
  }, [session.question, question]);

  function openAction() {
    if (!session.report) controller.restore("action", session.question || DEMO_QUESTION);
    else controller.openAction();
  }

  function restart() {
    controller.reset();
    setQuestion("");
  }

  return (
    <div className={styles.app}>
      <link rel="preconnect" href="https://fonts.googleapis.com" />
      <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
      <link rel="stylesheet" href={FONT_HREF} />

      <header className={styles.bar}>
        <button type="button" className={styles.brand} onClick={restart}>
          <SignalMark size={26} title="고객 시그널 캐처 홈" />
          <span className={styles.brandText}>
            <strong>캐치캐치</strong>
            <small>Customer Signal Catcher</small>
          </span>
        </button>
        <span className={styles.barSpacer} />
        <ExperimentMenu
          experiments={experiments.experiments}
          onOpen={(id) => {
            setActionId(id);
            openAction();
          }}
        />
        <span className={styles.barNote}>
          <em className={styles.barBadge}>PROTOTYPE</em>
          by 네박자
        </span>
      </header>

      <main className={styles.stage}>
        {session.phase === "ask" ? (
          <div key="ask" className={styles.enter}>
            <AskScreen
              question={question}
              onQuestionChange={setQuestion}
              onSubmit={() => controller.start(question)}
              notice={session.failureReason}
              suggestedQuestions={session.suggestedQuestions}
            />
          </div>
        ) : null}

        {session.phase === "catching" ? (
          <div key="catching" className={styles.enter}>
            <CatchingScreen
              session={session}
              bursting={controller.bursting}
              flatline={controller.flatline}
              frozen={Boolean(options.pause)}
              speed={options.speed}
              burstMark={options.burst}
              log={controller.log}
              onAnswerClarification={controller.answerClarification}
              onRetry={controller.retry}
              onGiveUp={restart}
            />
          </div>
        ) : null}

        {session.phase === "result" && session.report ? (
          <div key="result" className={styles.enter}>
            <ResultScreen
              report={session.report}
              outcome={session.outcome ?? "completed"}
              depth={depth}
              onDepthChange={setDepth}
              onOpenTrace={controller.openTrace}
              onRestart={restart}
              onOpenAction={(id) => {
                setActionId(id);
                controller.openAction();
              }}
              experimentOf={experiments.find}
            />
          </div>
        ) : null}

        {session.phase === "action" && ACTION_PLANS[actionId] ? (
          <div key="action" className={styles.enter}>
            <ActionScreen
              plan={ACTION_PLANS[actionId]}
              experiment={experiments.find(actionId)}
              conflict={experiments.conflictOf(
                ACTION_PLANS[actionId].segmentLabel,
                actionId,
              )}
              onApply={() =>
                experiments.begin({
                  actionId,
                  title: ACTION_PLANS[actionId].title,
                  segmentLabel: ACTION_PLANS[actionId].segmentLabel,
                  observeDays: ACTION_PLANS[actionId].observeDays,
                })
              }
              onAdvance={(days) => experiments.advance(actionId, days)}
              onComplete={(hits, total) => experiments.complete(actionId, hits, total)}
              onOpenNext={controller.closeAction}
              onBack={controller.closeAction}
            />
          </div>
        ) : null}

        {session.phase === "trace" && session.report ? (
          <div key="trace" className={styles.enter}>
            <TraceScreen
              report={session.report}
              question={session.question}
              onBack={controller.closeTrace}
            />
          </div>
        ) : null}
      </main>

      {/* 드로어와 모달이 붙는 자리. 전환 래퍼의 transform 밖이어야 한다. */}
      <div id={OVERLAY_ID} />
    </div>
  );
}
