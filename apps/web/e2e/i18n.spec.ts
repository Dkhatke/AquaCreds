import { mkdirSync } from "node:fs";
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

async function shot(page: import("@playwright/test").Page, name: string) {
  const dir = process.env.SCREENSHOT_DIR;
  if (!dir) return;
  mkdirSync(dir, { recursive: true });
  await page.screenshot({ path: `${dir}/${name}.png` });
}

const AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

test("language switch: Hindi hero, persisted across reload, English kept for honesty labels", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await page.getByLabel("Language").first().selectOption("hi");
  await expect(
    page.getByRole("heading", {
      level: 1,
      name: "तटीय पारिस्थितिकी तंत्र से सत्यापन-योग्य कार्बन प्रमाण तक।",
    }),
  ).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "hi");
  await shot(page, "landing-hi");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("lang", "hi");
  await expect(page.getByRole("link", { name: /डेमो देखें/ }).first()).toBeVisible();

  // Review status is stated, in English first.
  await expect(
    page.getByText(/AI-drafted translation that has not yet been reviewed/),
  ).toBeVisible();
  // Honesty label: English original stays visible beside the Hindi gloss.
  const footer = page.locator("footer");
  await expect(footer.getByText(/Estimated, pending accredited verification/)).toBeVisible();
  await expect(footer.getByText(/अनुमानित, मान्यता-प्राप्त सत्यापन लंबित/)).toBeVisible();
  // Untranslated sections fall back to English instead of showing keys.
  await expect(
    page.getByRole("heading", { name: "Collect, analyse, verify, record." }),
  ).toBeVisible();
});

test("language switch: Odia navigation and fallback", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Language").first().selectOption("or");
  await expect(page.locator("html")).toHaveAttribute("lang", "or");
  await expect(page.getByRole("link", { name: /ଡେମୋ ଦେଖନ୍ତୁ/ }).first()).toBeVisible();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("ଉପକୂଳୀୟ");
  await expect(page.locator("body")).not.toContainText("hero.title");
  await shot(page, "landing-or");
});

for (const locale of ["hi", "or"]) {
  test(`no axe violations on the landing page in ${locale}`, async ({ page, context, baseURL }) => {
    await context.addCookies([{ name: "aq_locale", value: locale, url: baseURL ?? "" }]);
    await page.goto("/");
    await expect(page.locator("html")).toHaveAttribute("lang", locale);
    const results = await new AxeBuilder({ page }).withTags(AXE_TAGS).analyze();
    expect(
      results.violations.map(
        (v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`,
      ),
    ).toEqual([]);
  });
}

test("an unknown locale cookie falls back to English", async ({ page, context, baseURL }) => {
  await context.addCookies([{ name: "aq_locale", value: "xx", url: baseURL ?? "" }]);
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
});
