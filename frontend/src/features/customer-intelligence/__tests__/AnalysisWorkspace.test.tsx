import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AnalysisWorkspace } from "../AnalysisWorkspace";
import type { ArtifactDocument, RunArtifact } from "../contracts";
import { initialRunState, runReducer } from "../run-reducer";
import { genericArtifact, genericDocument } from "./generic-fixtures";

const completedState = runReducer(initialRunState, {
  kind: "hydrate_artifact",
  artifact: genericArtifact as RunArtifact,
});

afterEach(cleanup);

describe("AnalysisWorkspace", () => {
  it("renders Goal, Plan, Fact, verified Note, document and downloads in order", () => {
    render(
      <AnalysisWorkspace
        state={completedState}
        document={genericDocument as ArtifactDocument}
        documentStatus="success"
        downloadUrls={{
          json: "/api/run-artifacts/run-1/download.json",
          markdown: "/api/run-artifacts/run-1/download.md",
        }}
        onOpenEvidence={vi.fn()}
      />,
    );

    const workspace = screen.getByRole("region", { name: "분석 Workspace" });
    expect(
      within(workspace).getByText("부정 피드백이 많은 Topic별 고객 신호를 비교한다."),
    ).toBeInTheDocument();
    expect(
      within(workspace).getByRole("heading", { name: "aggregate_events" }),
    ).toBeInTheDocument();
    expect(within(workspace).getByText("부정 피드백 수")).toBeInTheDocument();
    expect(within(workspace).getByText("12건")).toBeInTheDocument();
    expect(within(workspace).getByText(/스캔 100/)).toBeInTheDocument();
    expect(
      within(workspace).getByText("로밍 Topic의 부정 피드백은 12건입니다."),
    ).toBeInTheDocument();
    expect(
      within(workspace).getByText("aggregate_events:negative-feedback"),
    ).toBeInTheDocument();
    expect(within(workspace).getByText("EVD-DYNAMIC-1")).toBeInTheDocument();
    expect(
      within(workspace).getByRole("heading", {
        name: "로밍 Topic에서 부정 피드백 12건을 확인했습니다.",
      }),
    ).toBeInTheDocument();
    expect(
      within(workspace).getByRole("link", { name: "JSON 다운로드" }),
    ).toHaveAttribute("href", expect.stringContaining("download.json"));
    expect(
      within(workspace).getByRole("link", { name: "Markdown 다운로드" }),
    ).toHaveAttribute("href", expect.stringContaining("download.md"));
  });

  it("keeps a report-less degraded run readable with its persisted downloads", () => {
    render(
      <AnalysisWorkspace
        state={{
          ...initialRunState,
          runId: "run-degraded",
          phase: "degraded",
          status: "degraded",
          limitations: ["조건에 맞는 공개 데이터가 없습니다."],
        }}
        document={null}
        documentStatus="idle"
        downloadUrls={{
          json: "/api/run-artifacts/run-degraded/download.json",
          markdown: "/api/run-artifacts/run-degraded/download.md",
        }}
        onOpenEvidence={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "결론 없이 기록된 Run" })).toBeInTheDocument();
    expect(screen.getByText("조건에 맞는 공개 데이터가 없습니다.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "JSON 다운로드" })).toBeInTheDocument();
    expect(screen.queryByText(/protocol_error/)).not.toBeInTheDocument();
  });
});
