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

test("question to masked evidence working demo", async ({ page }) => {
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
      .getByText("Run 완료", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByLabel("완전한 Journey 패턴 고객 수"),
  ).toContainText("6명");
  await expectNoHorizontalPageScroll(page);

  await page
    .getByRole("button", { name: "CUST-003 Journey 보기" })
    .click();
  await expect(page.getByRole("heading", { name: "고객 Journey" })).toBeVisible();
  await expect(page.getByRole("list", { name: "CUST-003 고객 Journey" })).toBeVisible();

  const evidenceButton = page.getByRole("button", { name: /근거 보기/ }).first();
  await evidenceButton.click();
  const dialog = page.getByRole("dialog", { name: /Evidence ·/ });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("heading", { name: "원본 필드" })).toBeVisible();
  await expect(dialog.getByText("민감정보 마스킹 적용")).toBeVisible();

  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(evidenceButton).toBeFocused();
});

test("disabling VOC returns the truthful zero result", async ({ page }) => {
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
      .getByText("Run 완료", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByLabel("완전한 Journey 패턴 고객 수"),
  ).toContainText("0명");
  await expect(
    page.getByText("완전한 패턴 일치 고객이 없습니다."),
  ).toBeVisible();
  await expect(
    page.getByText(/VOC Source를 켜고 다시 분석하면/),
  ).toBeVisible();
  await expectNoHorizontalPageScroll(page);
});
