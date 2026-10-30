import { mkdirSync } from "node:fs";
import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";

const BASE = `http://localhost:${process.env.E2E_CONSOLE_PORT ?? 3171}`;
const SHOTS = process.env.SCREENSHOT_DIR;
test.use({ baseURL: BASE });
test.describe.configure({ mode: "serial" });

async function startDemo(page: Page) {
  const res = await page.request.post("/bff/auth/demo", { headers: { "x-aquacreds-csrf": "1" } });
  expect(res.ok()).toBeTruthy();
}

async function axe(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    // The basemap canvas is third-party WebGL; the project table is the accessible equivalent.
    .exclude(".maplibregl-map")
    .analyze();
  return results.violations.map(
    (v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`,
  );
}

async function shot(page: Page, name: string) {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
}

test.beforeEach(async ({ page }) => {
  await startDemo(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
});

test("overview shows projects, per-project status and what needs attention", async ({ page }) => {
  await page.goto("/console");
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
  const table = page.getByRole("table", { name: "Projects" });
  await expect(table.getByRole("link", { name: "Fixture Plot A, Bhitarkanika" })).toBeVisible();
  await expect(table.getByText("Changes requested").first()).toBeVisible();
  await expect(table.getByText(/ecosystem_classifier_v1\.1/).first()).toBeVisible();
  const attention = page.locator("section", {
    has: page.getByRole("heading", { name: "What needs your attention" }),
  });
  await expect(attention.getByText("1 open alert on Fixture Plot A, Bhitarkanika")).toBeVisible();
  await expect(attention.getByText("1 field submission failed the evidence checks")).toBeVisible();
  await expect(attention.getByText("You have 2 recovery codes left")).toBeVisible();
  await expect(
    attention.getByText(
      "Project Fixture Plot B, Mahanadi delta needs changes before it can proceed",
    ),
  ).toBeVisible();
  await expect(page.getByText("71.4")).toBeVisible();
  await expect(page.getByText("Score version stewardship-v2")).toBeVisible();
  await expect(
    page.getByText(/Carbon figures are estimated, pending accredited verification/).first(),
  ).toBeVisible();
  await page.waitForTimeout(1500);
  await shot(page, "overview-en");
  expect(await axe(page)).toEqual([]);
});

test("community portal: members, field activity, plain-language stewardship, tips", async ({
  page,
}) => {
  await page.goto("/console/community");
  await expect(page.getByRole("heading", { level: 1, name: "Community portal" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "Fixture Field Agent" })).toBeVisible();
  await expect(page.getByText(/does not say who submitted a photo/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Trust-score distribution" })).toBeVisible();
  await expect(page.getByText("90–100")).toBeVisible();
  await expect(page.getByText(/How steadily independent verifiers/).first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Getting good evidence" })).toBeVisible();
  await shot(page, "community-en");
  expect(await axe(page)).toEqual([]);
});

test("notifications: bell count, mark read, read-all, email preferences", async ({ page }) => {
  await page.goto("/console/notifications");
  const bell = page.getByRole("link", { name: /Notifications, 3 unread/ });
  await expect(bell).toBeVisible();
  await expect(page.getByText("Changes requested on Fixture Plot B")).toBeVisible();
  await shot(page, "notifications-en");
  expect(await axe(page)).toEqual([]);

  await page.getByRole("button", { name: "Mark as read" }).first().click();
  await expect(page.getByRole("link", { name: /Notifications, 2 unread/ })).toBeVisible({
    timeout: 10_000,
  });

  const box = page.getByRole("checkbox", { name: "Email" }).nth(4); // submission_rejected (off by default)
  await expect(box).not.toBeChecked();
  await box.click();
  await expect(box).toBeChecked();
  await expect(page.getByRole("button", { name: "Reset to default" })).toBeVisible();
  await page.getByRole("button", { name: "Reset to default" }).click();
  await expect(box).not.toBeChecked();

  await page.getByRole("button", { name: "Mark all as read" }).click();
  await expect(page.getByRole("link", { name: "Notifications", exact: true })).toBeVisible({
    timeout: 10_000,
  });
});

test("account security: MFA status and recovery-code regeneration needs a valid TOTP", async ({
  page,
}) => {
  await page.goto("/console/account");
  await expect(page.getByText(/^.?\s*Enabled$/)).toBeVisible();
  await expect(page.getByText("2 unused recovery codes")).toBeVisible();
  await expect(page.getByText(/Only 2 left/)).toBeVisible();
  await shot(page, "account-en");
  expect(await axe(page)).toEqual([]);

  await page.getByRole("button", { name: "Regenerate recovery codes" }).click();
  await page.getByLabel("Current 6-digit authenticator code").fill("000000");
  await page.getByRole("button", { name: "Regenerate", exact: true }).click();
  await expect(page.getByText("Invalid authentication code")).toBeVisible();
  await page.getByLabel("Current 6-digit authenticator code").fill("123456");
  await page.getByRole("button", { name: "Regenerate", exact: true }).click();
  await expect(page.getByText("Your new recovery codes")).toBeVisible();
  await expect(page.getByText("FIXT-1000-CODE")).toBeVisible();
  await page.getByRole("button", { name: "I have saved them" }).click();
  await expect(page.getByText("10 unused recovery codes")).toBeVisible();
});

test("language switch in the console: Hindi navigation, English kept on carbon label", async ({
  page,
}) => {
  await page.goto("/console");
  await page.getByLabel("Language").selectOption("hi");
  const nav = page.getByRole("navigation", { name: "कंसोल" });
  await expect(nav.getByRole("link", { name: "अवलोकन" })).toBeVisible();
  await expect(nav.getByRole("link", { name: "समुदाय पोर्टल" })).toBeVisible();
  await expect(
    page.getByText("Carbon figures are estimated, pending accredited verification.").first(),
  ).toBeVisible();
  await expect(page.getByText(/कार्बन के आँकड़े अनुमानित हैं/).first()).toBeVisible();
  // Not yet translated: falls back to English.
  await expect(
    page.getByText("Your projects at a glance, built only from live API data."),
  ).toBeVisible();
  await page.waitForTimeout(1500);
  await shot(page, "overview-hi");
  expect(await axe(page)).toEqual([]);
  await page.getByLabel("Language").selectOption("or");
  await expect(
    page.getByRole("navigation", { name: "କନସୋଲ୍" }).getByRole("link", { name: "ସାରାଂଶ" }),
  ).toBeVisible();
  await shot(page, "overview-or");
});
