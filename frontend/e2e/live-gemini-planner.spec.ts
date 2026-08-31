import { expect, test } from "@playwright/test";

const backendUrl = `http://127.0.0.1:${process.env.E2E_BACKEND_PORT ?? "38100"}`;
const question =
  "최근 부정적인 피드백을 남긴 고객은 이후 어떤 행동 패턴을 보이고, 일반 고객과 무엇이 달라?";

test.skip(
  process.env.RUN_LIVE_GEMINI !== "1",
  "실제 Gemini 호출은 RUN_LIVE_GEMINI=1일 때만 실행합니다.",
);

test("자유 질문을 동적 Plan으로 분석하고 공개 기록을 남긴다", async ({ page }) => {
  test.setTimeout(180_000);
  await page.goto("/legacy");
  await page
    .getByRole("textbox", { name: "분석 질문", exact: true })
    .fill(question);
  const acceptedResponse = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/runs" &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "분석 시작" }).click();
  const accepted = (await (await acceptedResponse).json()) as {
    run_id: string;
  };

  const trace = page.getByRole("list", { name: "공개 Agent 실행 기록" });
  await expect(trace.getByText("분석을 마쳤습니다", { exact: true })).toBeVisible({
    timeout: 150_000,
  });

  const workspace = page.getByRole("region", { name: "분석 Workspace" });
  await expect(
    workspace.getByRole("heading", { name: "실행 계획" }),
  ).toBeVisible();
  await expect(
    workspace.getByText("선택 근거", { exact: true }).first(),
  ).toBeVisible();
  await expect(
    workspace.getByText("출력 (Tool Output) · 검증 Fact", { exact: true }).first(),
  ).toBeVisible();
  await expect(
    workspace.getByRole("region", { name: "관찰 Fact" }).first(),
  ).toBeVisible();
  await expect(
    workspace.getByRole("region", { name: "다음 행동" }).first(),
  ).toBeVisible();
  await expect(workspace.locator(".document-card")).toBeVisible();
  await expect(
    page
      .getByRole("region", { name: "이전 Run" })
      .getByRole("button", { name: new RegExp(question.slice(0, 12)) })
      .first(),
  ).toBeVisible();

  const artifactResponse = await page.request.get(
    `${backendUrl}/api/run-artifacts/${accepted.run_id}`,
  );
  expect(artifactResponse.ok()).toBe(true);
  const artifact = (await artifactResponse.json()) as {
    plan_history: Array<{
      revision: number;
      steps: Array<{ primitive: string; source_ids: string[] }>;
    }>;
    facts: unknown[];
    notes: Array<{ next_action: string }>;
    report: unknown;
  };
  expect(artifact.plan_history.length).toBeGreaterThanOrEqual(1);
  expect(artifact.plan_history.at(-1)?.steps.length).toBeGreaterThanOrEqual(2);
  expect(artifact.facts.length).toBeGreaterThanOrEqual(1);
  expect(artifact.notes.length).toBeGreaterThanOrEqual(1);
  expect(artifact.notes.every((note) => note.next_action.length > 0)).toBe(true);
  expect(artifact.report).not.toBeNull();

  const documentResponse = await page.request.get(
    `${backendUrl}/api/run-artifacts/${accepted.run_id}/document`,
  );
  expect(documentResponse.ok()).toBe(true);
  const markdownResponse = await page.request.get(
    `${backendUrl}/api/run-artifacts/${accepted.run_id}/download.md`,
  );
  expect(markdownResponse.ok()).toBe(true);
  const markdown = await markdownResponse.text();
  expect(markdown).toContain("## 분석 계획");
  expect(markdown).toContain("## 공개 Facts");
  expect(markdown).toContain("- Next action:");
});
