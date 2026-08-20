import { readFile } from "node:fs/promises";

import { expect, test, type Page } from "@playwright/test";

const NEGATIVE_TOPIC_QUESTION =
  "최근 부정 피드백이 많은 Topic과 관련 고객 Segment를 알려줘.";
const REPEAT_JOURNEY_QUESTION =
  "반복 행동 뒤 상담으로 전환되는 Journey를 보여줘.";
const SIGNUP_ABANDONMENT_QUESTION =
  "가입 시작 뒤 완료하지 못한 고객과 이탈 단계를 알려줘.";
const AMBIGUOUS_QUESTION = "최근 고객 신호를 분석해줘.";

const cases = [
  {
    question: NEGATIVE_TOPIC_QUESTION,
    objective: "부정 피드백이 집중된 Topic과 고객 규모를 확인합니다.",
    primitive: "aggregate_events",
    metricLabel: "Negative Feedback Customer Count",
    expectedValue: 6,
  },
  {
    question: REPEAT_JOURNEY_QUESTION,
    objective: "반복 행동 뒤 상담으로 이어진 고객 Journey를 확인합니다.",
    primitive: "match_sequence",
    metricLabel: "Matched Customer Count",
    expectedValue: 6,
  },
  {
    question: SIGNUP_ABANDONMENT_QUESTION,
    objective: "가입을 시작했지만 완료하지 않은 고객과 이탈 단계를 확인합니다.",
    primitive: "segment_customers",
    metricLabel: "Abandoned Customer Count",
    expectedValue: 5,
  },
] as const;

function escapeRegex(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function expectNoHorizontalPageScroll(page: Page) {
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          Math.max(
            document.documentElement.scrollWidth,
            document.body.scrollWidth,
          ) <= window.innerWidth,
      ),
    )
    .toBe(true);
}

async function runQuestion(page: Page, question: string) {
  await page.goto("/");
  await page
    .getByRole("button", { name: new RegExp(escapeRegex(question)) })
    .click();
  await expect(
    page.getByRole("textbox", { name: "분석 질문", exact: true }),
  ).toHaveValue(question);
  await page.getByRole("button", { name: "분석 시작" }).click();
  await expect(
    page
      .getByRole("list", { name: "공개 Agent 실행 기록" })
      .getByText("Run 완료", { exact: true }),
  ).toBeVisible();
  return page.getByRole("region", { name: "분석 Workspace" });
}

for (const analysisCase of cases) {
  test(`${analysisCase.expectedValue}명 결과와 공개 Step Note를 표시한다: ${analysisCase.question}`, async ({
    page,
    isMobile,
  }) => {
    const workspace = await runQuestion(page, analysisCase.question);

    await expect(
      workspace.getByRole("heading", { name: analysisCase.objective }),
    ).toBeVisible();
    await expect(
      workspace
        .getByRole("heading", { name: analysisCase.primitive, exact: true })
        .first(),
    ).toBeVisible();
    await expect(
      workspace.getByText(analysisCase.metricLabel, { exact: true }).first(),
    ).toBeVisible();
    await expect(
      workspace
        .getByText(`${analysisCase.expectedValue}customers`, { exact: true })
        .first(),
    ).toBeVisible();
    await expect(
      workspace.getByRole("heading", { name: "Analysis Note" }),
    ).toBeVisible();
    await expect(
      workspace
        .getByText(
          `${analysisCase.metricLabel}: ${analysisCase.expectedValue} customers`,
          { exact: true },
        )
        .first(),
    ).toBeVisible();
    await expect(
      workspace.getByText("선택 근거", { exact: true }).first(),
    ).toBeVisible();
    await expect(
      workspace.getByText("검증 Fact", { exact: true }).first(),
    ).toBeVisible();
    await expect(
      workspace.getByRole("region", { name: "관찰 Fact" }).first(),
    ).toBeVisible();
    await expect(
      workspace.getByRole("region", { name: "다음 행동" }).first(),
    ).toBeVisible();

    const chat = page.getByRole("complementary", { name: "질문과 Run 기록" });
    const [chatBox, workspaceBox] = await Promise.all([
      chat.boundingBox(),
      workspace.boundingBox(),
    ]);
    expect(chatBox).not.toBeNull();
    expect(workspaceBox).not.toBeNull();
    if (isMobile) {
      expect(workspaceBox!.y).toBeGreaterThanOrEqual(
        chatBox!.y + chatBox!.height - 1,
      );
    } else {
      expect(workspaceBox!.x).toBeGreaterThan(chatBox!.x);
    }
    await expectNoHorizontalPageScroll(page);
  });
}

test("저장된 Run을 새로고침 뒤 열고 JSON과 Markdown을 다운로드한다", async ({
  page,
  isMobile,
}) => {
  test.skip(Boolean(isMobile), "다운로드 파일 검증은 desktop Chromium에서 한 번 실행합니다.");
  await runQuestion(page, NEGATIVE_TOPIC_QUESTION);

  await page.reload();
  const history = page.getByRole("region", { name: "이전 Run" });
  const historyItem = history
    .getByRole("button", {
      name: new RegExp(escapeRegex(NEGATIVE_TOPIC_QUESTION)),
    })
    .first();
  await expect(historyItem).toBeVisible();
  await historyItem.click();
  await expect(
    page.getByRole("heading", {
      name: "부정 피드백이 집중된 Topic과 고객 규모를 확인합니다.",
    }),
  ).toBeVisible();

  const [jsonDownload] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("link", { name: "JSON 다운로드" }).click(),
  ]);
  expect(jsonDownload.suggestedFilename()).toMatch(/^[0-9a-f-]+\.json$/);
  const jsonPath = await jsonDownload.path();
  expect(jsonPath).not.toBeNull();
  const artifact = JSON.parse(await readFile(jsonPath!, "utf8")) as {
    request: { question: string };
    plan_history: unknown[];
    facts: unknown[];
    notes: Array<{ next_action: string }>;
  };
  expect(artifact.request.question).toBe(NEGATIVE_TOPIC_QUESTION);
  expect(artifact.plan_history.length).toBeGreaterThanOrEqual(1);
  expect(artifact.facts.length).toBeGreaterThanOrEqual(1);
  expect(artifact.notes.length).toBeGreaterThanOrEqual(1);
  expect(artifact.notes.every((note) => note.next_action.length > 0)).toBe(true);

  const [markdownDownload] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("link", { name: "Markdown 다운로드" }).click(),
  ]);
  expect(markdownDownload.suggestedFilename()).toMatch(/^[0-9a-f-]+\.md$/);
  const markdownPath = await markdownDownload.path();
  expect(markdownPath).not.toBeNull();
  const markdown = await readFile(markdownPath!, "utf8");
  expect(markdown).toContain(NEGATIVE_TOPIC_QUESTION);
  expect(markdown).toContain("## 분석 계획");
  expect(markdown).toContain("revision 0");
  expect(markdown).toContain("## 공개 Facts");
  expect(markdown).toContain("- Next action:");
});

test("확인 답변 뒤 같은 Run에서 분석을 계속한다", async ({ page }) => {
  await page.goto("/");
  await page
    .getByRole("textbox", { name: "분석 질문", exact: true })
    .fill(AMBIGUOUS_QUESTION);
  const createResponsePromise = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/runs" &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "분석 시작" }).click();
  const accepted = (await (await createResponsePromise).json()) as {
    run_id: string;
  };

  const clarification = page.getByRole("textbox", { name: "확인 답변" });
  await expect(clarification).toBeFocused();
  await clarification.fill(NEGATIVE_TOPIC_QUESTION);
  const clarificationResponsePromise = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname ===
        `/api/runs/${accepted.run_id}/clarification` &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "답변하고 계속" }).click();
  const resumed = (await (await clarificationResponsePromise).json()) as {
    run_id: string;
  };
  expect(resumed.run_id).toBe(accepted.run_id);

  await expect(
    page
      .getByRole("list", { name: "공개 Agent 실행 기록" })
      .getByText("Run 완료", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("6customers", { exact: true }).first(),
  ).toBeVisible();
});

test("mobile에서는 Chat 다음에 Workspace가 오고 가로 스크롤이 없다", async ({
  page,
  isMobile,
}) => {
  test.skip(!isMobile, "mobile 프로젝트 전용 검증입니다.");
  await page.goto("/");
  const chat = page.getByRole("complementary", { name: "질문과 Run 기록" });
  const workspace = page.getByRole("region", { name: "분석 Workspace" });
  await expect(chat).toBeVisible();
  await expect(workspace).toBeVisible();
  expect(
    await chat.evaluate(
      (chatNode, workspaceNode) =>
        Boolean(
          chatNode.compareDocumentPosition(workspaceNode as Node) &
            Node.DOCUMENT_POSITION_FOLLOWING,
        ),
      await workspace.elementHandle(),
    ),
  ).toBe(true);
  await expectNoHorizontalPageScroll(page);
});
