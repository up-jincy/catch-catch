import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { QueryComposer } from "../QueryComposer";


describe("QueryComposer source selector", () => {
  it("shows all five customer journey source families", () => {
    render(
      <QueryComposer
        question=""
        startDate="2026-07-20"
        endDate="2026-08-19"
        enabledSources={["search_history", "search_feedback", "voc"]}
        isCreating={false}
        runPhase="idle"
        submissionError={null}
        onQuestionChange={vi.fn()}
        onStartDateChange={vi.fn()}
        onEndDateChange={vi.fn()}
        onToggleSource={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );

    expect(screen.getByRole("checkbox", { name: /검색 이력/ })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /검색 피드백/ })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /디지털 행동/ })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /가입 정보/ })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /VOC/ })).toBeInTheDocument();
  });
});
