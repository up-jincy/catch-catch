import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ChatPanel } from "../ChatPanel";
import { initialRunState } from "../run-reducer";
import { KNOWN_SOURCE_OPTIONS } from "../source-catalog";

const composerProps = {
  question: "최근 고객 신호를 분석해줘.",
  startDate: "2026-07-20",
  endDate: "2026-08-19",
  enabledSources: ["search_history", "voc"],
  sourceOptions: KNOWN_SOURCE_OPTIONS,
  isCreating: false,
  runPhase: "awaiting_clarification",
  submissionError: null,
  onQuestionChange: vi.fn(),
  onStartDateChange: vi.fn(),
  onEndDateChange: vi.fn(),
  onToggleSource: vi.fn(),
  onSubmit: vi.fn(),
};

afterEach(cleanup);

describe("ChatPanel", () => {
  it("announces the public run log and focuses a same-run clarification answer", async () => {
    const user = userEvent.setup();
    const onSubmitClarification = vi.fn();
    render(
      <ChatPanel
        composerProps={composerProps}
        state={{
          ...initialRunState,
          runId: "run-clarification",
          phase: "awaiting_clarification",
          status: "awaiting_clarification",
          request: {
            question: composerProps.question,
            start_at: "2026-07-20T00:00:00+09:00",
            end_at: "2026-08-19T00:00:00+09:00",
            enabled_sources: ["search_history", "voc"],
          },
          clarification: {
            kind: "clarification",
            clarification_id: "clarification-1",
            question: "어떤 고객 신호를 분석할까요?",
            answer: null,
            requested_at: null,
            answered_at: null,
          },
        }}
        isCreating={false}
        clarificationError={null}
        onSubmitClarification={onSubmitClarification}
        history={<div>최근 Run</div>}
      />,
    );

    expect(screen.getByRole("log", { name: "분석 대화" })).toHaveTextContent(
      "어떤 고객 신호를 분석할까요?",
    );
    const answer = screen.getByRole("textbox", { name: "확인 답변" });
    expect(answer).toHaveFocus();

    await user.type(answer, "최근 30일 부정 피드백");
    await user.click(screen.getByRole("button", { name: "답변하고 계속" }));

    expect(onSubmitClarification).toHaveBeenCalledWith(
      "최근 30일 부정 피드백",
    );
  });

  it("shows safe unsupported suggestions without private provider content", () => {
    render(
      <ChatPanel
        composerProps={{ ...composerProps, runPhase: "failed" }}
        state={{
          ...initialRunState,
          runId: "run-unsupported",
          phase: "failed",
          status: "failed",
          error: {
            code: "unsupported_analysis",
            message: "현재 안전한 분석 범위에서 지원하지 않는 요청입니다.",
            suggested_questions: ["부정 피드백 Topic을 비교해 줘"],
          },
          suggestedQuestions: ["부정 피드백 Topic을 비교해 줘"],
        }}
        isCreating={false}
        clarificationError={null}
        onSubmitClarification={vi.fn()}
        history={null}
      />,
    );

    expect(
      screen.getByText("현재 안전한 분석 범위에서 지원하지 않는 요청입니다."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "부정 피드백 Topic을 비교해 줘" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/provider|reasoning|chain.of.thought/i)).not.toBeInTheDocument();
  });
});
