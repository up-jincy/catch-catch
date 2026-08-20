import { describe, expect, it } from "vitest";

import type { AnyRunStreamEvent, RunStreamEvent } from "../contracts";
import { sourceLabel } from "../source-catalog";
import { selectVisibleRunEvents } from "../run-selectors";

describe("generic run selectors", () => {
  it("keeps manifest-defined source IDs while labeling known legacy sources", () => {
    expect(sourceLabel("search_history")).toBe("검색 이력");
    expect(sourceLabel("support_chat_v2")).toBe("support_chat_v2");
  });

  it("prefers generic step events over duplicate legacy tool events", () => {
    const events: AnyRunStreamEvent[] = [
      {
        id: 1,
        type: "tool_started",
        data: { tool: "aggregate_events", source: ["support_chat_v2"] },
      },
      {
        id: 2,
        type: "step_started",
        data: {
          step_id: "step-aggregate",
          primitive: "aggregate_events",
        },
      },
      {
        id: 3,
        type: "tool_completed",
        data: {
          tool: "aggregate_events",
          source: ["support_chat_v2"],
          count: 1,
          duration_ms: 10,
          result_id: "aggregate_events:1",
        },
      },
      {
        id: 4,
        type: "step_completed",
        data: {
          step_id: "step-aggregate",
          status: "completed",
          result_ids: ["aggregate_events:1"],
          duration_ms: 10,
        },
      },
    ];

    expect(selectVisibleRunEvents(events).map((event) => event.type)).toEqual([
      "step_started",
      "step_completed",
    ]);
  });

  it("retains the complete legacy trace when no generic step event exists", () => {
    const events: RunStreamEvent[] = [
      {
        id: 1,
        type: "tool_started",
        data: { tool: "match_journey_pattern", source: ["search_history"] },
      },
      {
        id: 2,
        type: "tool_completed",
        data: {
          tool: "match_journey_pattern",
          source: ["search_history"],
          count: 6,
          duration_ms: 18,
          result_id: "match_journey_pattern:1",
        },
      },
    ];

    expect(selectVisibleRunEvents(events)).toEqual(events);
  });
});
