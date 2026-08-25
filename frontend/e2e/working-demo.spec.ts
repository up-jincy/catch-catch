import { expect, test, type Page } from "@playwright/test";

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

test("기존 Journey 문구도 단일 Analysis Agent로 분석한다", async ({ page }) => {
  await page.goto("/");

  const question = page.getByRole("textbox", {
    name: "분석 질문",
    exact: true,
  });
  await question.fill("AI 검색 실패 후 고객센터까지 문의한 고객이 얼마나 돼?");
  await expect(question).toHaveValue(
    "AI 검색 실패 후 고객센터까지 문의한 고객이 얼마나 돼?",
  );
  await page.getByRole("button", { name: "분석 시작" }).click();

  await expect(
    page
      .getByRole("list", { name: "공개 Agent 실행 기록" })
      .getByText("분석을 마쳤습니다", { exact: true }),
  ).toBeVisible();
  const workspace = page.getByRole("region", { name: "분석 Workspace" });
  await expect(
    workspace.getByRole("heading", {
      name: "반복 행동 뒤 상담으로 이어진 고객 Journey를 확인합니다.",
    }),
  ).toBeVisible();
  await expect(
    workspace
      .getByRole("heading", { name: "행동 순서 매칭", exact: true })
      .first(),
  ).toBeVisible();
  await expect(
    workspace.getByText("Matched Customer Count", { exact: true }).first(),
  ).toBeVisible();
  await expect(
    workspace.getByText("6customers", { exact: true }).first(),
  ).toBeVisible();
  await expect(
    workspace.getByRole("heading", { name: "Analysis Note" }),
  ).toBeVisible();
  await expect(
    workspace.getByText("출력 (Tool Output) · 검증 Fact", { exact: true }).first(),
  ).toBeVisible();
  await expectNoHorizontalPageScroll(page);
});

test("VOC를 끄면 검증된 0명 결과에서 후속 조회를 멈춘다", async ({ page }) => {
  const sourceCatalogLoaded = page.waitForResponse(
    (response) => response.url().endsWith("/api/sources") && response.ok(),
  );
  await page.goto("/");
  await sourceCatalogLoaded;

  await page
    .getByRole("textbox", { name: "분석 질문", exact: true })
    .fill("AI 검색 실패 후 고객센터까지 문의한 고객이 얼마나 돼?");
  const vocCheckbox = page.getByRole("checkbox", {
    name: /VOC|Voice of customer/i,
  });
  await vocCheckbox.press("Space");
  await expect(vocCheckbox).not.toBeChecked();
  await page.getByRole("button", { name: "분석 시작" }).click();

  await expect(
    page
      .getByRole("list", { name: "공개 Agent 실행 기록" })
      .getByText("분석을 마쳤습니다", { exact: true }),
  ).toBeVisible();
  const workspace = page.getByRole("region", { name: "분석 Workspace" });
  await expect(
    workspace.getByText("Matched Customer Count", { exact: true }).first(),
  ).toBeVisible();
  await expect(
    workspace.getByText("0customers", { exact: true }).first(),
  ).toBeVisible();
  await expect(
    workspace.getByRole("heading", { name: "get_customer_journey", exact: true }),
  ).toHaveCount(0);
  await expectNoHorizontalPageScroll(page);
});
