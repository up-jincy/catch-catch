import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ArtifactSummary } from "../contracts";
import { RunHistory } from "../RunHistory";

const items: ArtifactSummary[] = [
  {
    run_id: "11111111-1111-4111-8111-111111111111",
    status: "completed",
    question: "부정 피드백이 많은 Topic을 알려줘",
    headline: "로밍 Topic의 부정 피드백 12건",
    created_at: "2026-08-20T01:00:00Z",
    updated_at: "2026-08-20T01:00:01Z",
    completed_at: "2026-08-20T01:00:01Z",
    error_code: null,
  },
  {
    run_id: "22222222-2222-4222-8222-222222222222",
    status: "failed",
    question: "원본 전체를 내려줘",
    headline: "지원하지 않는 분석",
    created_at: "2026-08-20T00:00:00Z",
    updated_at: "2026-08-20T00:00:01Z",
    completed_at: "2026-08-20T00:00:01Z",
    error_code: "unsupported_analysis",
  },
];

afterEach(cleanup);

describe("RunHistory", () => {
  it("marks and selects a persisted run accessibly", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(
      <RunHistory
        items={items}
        status="success"
        selectedRunId={items[0].run_id}
        onSelect={onSelect}
      />,
    );

    expect(
      screen.getByRole("button", { name: /로밍 Topic의 부정 피드백 12건/ }),
    ).toHaveAttribute("aria-current", "true");

    await user.click(
      screen.getByRole("button", { name: /지원하지 않는 분석/ }),
    );
    expect(onSelect).toHaveBeenCalledWith(items[1].run_id);
  });
});
