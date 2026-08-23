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
      within(workspace).getAllByRole("heading", { name: "이벤트 집계" }).length,
    ).toBeGreaterThan(0);
    expect(within(workspace).getAllByText("입력 (Tool Input)").length).toBeGreaterThan(0);
    expect(
      within(workspace).getAllByText(/출력 \(Tool Output\)/).length,
    ).toBeGreaterThan(0);
    expect(
      within(workspace).getByText(
        "집계 Fact를 반영해 고객 정렬 단계를 구체화했습니다.",
      ),
    ).toBeInTheDocument();
    expect(within(workspace).getByText("revision 0 → 1")).toBeInTheDocument();
    expect(
      within(workspace).getByText("Topic별 부정 피드백 규모를 검증합니다."),
    ).toBeInTheDocument();
    expect(
      within(workspace).getByText(/"group_by":\["topic"\]/),
    ).toBeInTheDocument();
    expect(within(workspace).getAllByText("support_chat_v2").length).toBeGreaterThan(0);
    expect(within(workspace).getAllByText("부정 피드백 수").length).toBeGreaterThan(0);
    expect(within(workspace).getByText("12건")).toBeInTheDocument();
    expect(within(workspace).getByText(/이벤트 100건 스캔/)).toBeInTheDocument();
    expect(within(workspace).getByText("관찰 Fact")).toBeInTheDocument();
    expect(
      within(workspace).getAllByText("로밍 Topic의 부정 피드백은 12건입니다.").length,
    ).toBeGreaterThan(0);
    expect(
      within(workspace).getByText("무엇을 알게 됐나 — 검증된 발견"),
    ).toBeInTheDocument();
    expect(within(workspace).getByText("핵심 지표")).toBeInTheDocument();
    expect(within(workspace).getByText("다음 행동")).toBeInTheDocument();
    expect(
      within(workspace).getByText(
        "검증된 Topic 집계를 기준으로 고객 신호를 정렬합니다.",
      ),
    ).toBeInTheDocument();
    expect(
      within(workspace).getByText("aggregate_events:negative-feedback"),
    ).toBeInTheDocument();
    expect(within(workspace).getByText("dataset-v2")).toBeInTheDocument();
    expect(within(workspace).getByText(/adapter-v2/)).toBeInTheDocument();
    expect(within(workspace).getByText(/manifest-v2/)).toBeInTheDocument();
    expect(within(workspace).getByText(/2026-07-20T00:00:00\+09:00/)).toBeInTheDocument();
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
