import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AgentTrace } from "../AgentTrace";

describe("AgentTrace", () => {
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
