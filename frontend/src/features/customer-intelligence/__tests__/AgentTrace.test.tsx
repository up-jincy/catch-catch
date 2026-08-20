import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { AgentTrace } from "../AgentTrace";

afterEach(cleanup);

describe("AgentTrace", () => {
  it("shows the public step selection reason when a generic step starts", () => {
    render(
      <AgentTrace
        phase="running"
        fallbackReason={null}
        events={[
          {
            id: 1,
            type: "step_started",
            data: {
              step_id: "step-aggregate",
              primitive: "aggregate_events",
              selection_reason: "Topic별 부정 피드백 규모를 검증합니다.",
              objective: "이 문구보다 선택 근거가 우선해야 합니다.",
            },
          },
        ]}
      />,
    );

    expect(
      screen.getByText("Topic별 부정 피드백 규모를 검증합니다."),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("이 문구보다 선택 근거가 우선해야 합니다."),
    ).not.toBeInTheDocument();
  });

  it("announces tool start and completion with distinct state and count", () => {
    const { rerender } = render(
      <AgentTrace
        phase="running"
        fallbackReason={null}
        events={[
          {
            id: 1,
            type: "tool_started",
            data: {
              tool: "match_journey_pattern",
              source: ["search_history", "voc"],
            },
          },
        ]}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent(
      "Journey 패턴 매칭 시작 · 검색 이력 · VOC",
    );

    rerender(
      <AgentTrace
        phase="running"
        fallbackReason={null}
        events={[
          {
            id: 2,
            type: "tool_completed",
            data: {
              tool: "match_journey_pattern",
              source: ["search_history", "voc"],
              count: 6,
              duration_ms: 18,
              result_id: "private-result",
            },
          },
        ]}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent(
      "Journey 패턴 매칭 완료 · 6건 · 18ms",
    );
    expect(screen.queryByText("private-result")).not.toBeInTheDocument();
  });
});
