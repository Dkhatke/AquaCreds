import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";

// These tests run against a Next server wired to e2e/mock-api.mjs (see playwright.config.ts).
const P1 = "11111111-1111-4111-8111-111111111111";
const HASH = "a".repeat(64);
const SHOTS = process.env.SCREENSHOT_DIR;

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
}

async function axe(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .analyze();
  expect(
    results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`),
  ).toEqual([]);
}

async function signIn(page: Page, demo = false) {
  const url = `http://localhost:${process.env.E2E_MOCKED_PORT ?? 3721}`;
  await page
    .context()
    .addCookies([
      { name: "aq_at", value: "test-token", url },
      ...(demo ? [{ name: "aq_demo", value: "1", url }] : []),
    ]);
}

test.describe("public registry (no login)", () => {
  test("projects list and detail", async ({ page }) => {
    await page.goto("/registry");
    await expect(page.getByRole("heading", { level: 1, name: "Projects" })).toBeVisible();
    await page.getByRole("link", { name: "Test Mangrove Restoration" }).click();
    await expect(page).toHaveURL(new RegExp(`/registry/projects/${P1}`));
    await expect(
      page.getByRole("img", { name: "Boundary of Test Mangrove Restoration" }),
    ).toBeVisible();
    await expect(page.getByRole("heading", { name: "Approved MRV reports" })).toBeVisible();
    await expect(page.getByText("Context only.")).toBeVisible();
    await expect(page.getByRole("link", { name: /PDF/ })).toHaveAttribute(
      "href",
      /\/bff\/api\/v1\/public\/reports\/.+\/report\.pdf/,
    );
    await shot(page, "public-registry-project");
    await axe(page);
  });

  test("vintages and retirement certificate with QR", async ({ page }) => {
    await page.goto("/registry/vintages");
    await expect(page.getByRole("cell", { name: "tCCC" }).first()).toBeVisible();
    await page.goto("/registry/retirements");
    await page.getByRole("link", { name: "7", exact: true }).click();
    await expect(page.getByRole("img", { name: /QR code/ })).toBeVisible();
    await expect(page.getByRole("link", { name: /Download certificate/ })).toHaveAttribute(
      "href",
      "/bff/api/v1/public/retirements/7/certificate.pdf",
    );
    await expect(page.getByRole("link", { name: /Explorer/ })).toBeVisible();
    await shot(page, "public-registry-retirement");
    await axe(page);
  });

  test("verify a report hash", async ({ page }) => {
    await page.goto("/registry/verify");
    await page.getByLabel("Report hash (SHA-256)").fill("not-a-hash");
    await page.getByRole("button", { name: "Verify" }).click();
    await expect(page.getByText("64 hexadecimal characters")).toBeVisible();
    await page.getByLabel("Report hash (SHA-256)").fill(`0x${HASH}`);
    await page.getByRole("button", { name: "Verify" }).click();
    await expect(page.getByText(/Found: 1 matching report/)).toBeVisible();
    await shot(page, "public-registry-verify");
    await page.getByLabel("Report hash (SHA-256)").fill("c".repeat(64));
    await page.getByRole("button", { name: "Verify" }).click();
    await expect(page.getByText("No report with this hash")).toBeVisible();
    await axe(page);
  });

  test("marketplace hides unavailable listings by default", async ({ page }) => {
    await page.goto("/marketplace");
    await expect(page.getByRole("article")).toHaveCount(1);
    await expect(page.getByText("72.4").first()).toBeVisible();
    await expect(page.getByText("stewardship_v1").first()).toBeVisible();
    await expect(page.getByRole("button", { name: "Connect wallet (coming soon)" })).toBeDisabled();
    await page.getByLabel("Show unavailable listings").check();
    await expect(page.getByRole("article")).toHaveCount(2);
    await expect(page.getByText("Sold out").first()).toBeVisible();
    await shot(page, "public-marketplace");
    await axe(page);
    await page.getByRole("tab", { name: "Trades and prices" }).click();
    await expect(page.getByRole("img", { name: /Price per unit/ })).toBeVisible();
  });
});

test.describe("console sections", () => {
  test("project registry: list, detail and API validation messages on submit", async ({ page }) => {
    await signIn(page);
    await page.goto("/console/registry");
    await expect(page.getByRole("link", { name: "Register a project" })).toBeVisible();
    await expect(page.getByText("No documents")).toBeVisible();
    await shot(page, "console-registry");
    await axe(page);
    await page.getByRole("link", { name: "Test Mangrove Restoration" }).click();
    await page.getByRole("button", { name: "Submit project" }).click();
    await expect(
      page.getByText("Required tenure document missing: forest_dept_agreement"),
    ).toBeVisible();
    await expect(page.getByText("Overlaps Other Project by 3.2 ha")).toBeVisible();
    await shot(page, "console-registry-detail");
    await axe(page);
  });

  test("new project form is available to org admins", async ({ page }) => {
    await signIn(page);
    await page.goto("/console/registry/new");
    await expect(page.getByRole("heading", { name: "Register a project" })).toBeVisible();
    await page.getByLabel("Project name").fill("Form check");
    await page.getByLabel(/^Boundary/).fill("{not json");
    await page.getByLabel("State").fill("Odisha");
    await page.getByLabel("Start date").fill("2025-03-01");
    await page.getByLabel(/Planned mangrove share/).fill("95");
    await page.getByLabel(/Soil disturbance/).fill("4");
    await page.getByRole("button", { name: "Create draft project" }).click();
    await expect(page.getByText("The boundary is not valid JSON")).toBeVisible();
    await axe(page);
  });

  test("demo session is read-only", async ({ page }) => {
    await signIn(page, true);
    await page.goto("/console/registry");
    await expect(page.getByText(/The demo is read-only/)).toBeVisible();
    await expect(page.getByRole("link", { name: "Register a project" })).toHaveCount(0);
    await page.goto(`/console/registry/${P1}`);
    await expect(page.getByRole("button", { name: "Submit project" })).toHaveCount(0);
  });

  test("blockchain records show relayed transaction statuses", async ({ page }) => {
    await signIn(page);
    await page.goto("/console/chain");
    await expect(page.getByRole("heading", { name: "Blockchain records" })).toBeVisible();
    await expect(page.getByText("Waiting for gas")).toBeVisible();
    await expect(page.getByText("Confirmed").first()).toBeVisible();
    await expect(page.getByRole("heading", { name: "Gas top-ups" })).toBeVisible();
    await expect(page.getByRole("link", { name: /Explorer/ }).first()).toHaveAttribute(
      "href",
      /amoy\.polygonscan\.com\/tx\//,
    );
    await shot(page, "console-chain");
    await axe(page);
  });

  test("marketplace: listings, trades and forward funding with the buyer-risk notice", async ({
    page,
  }) => {
    await signIn(page);
    await page.goto("/console/marketplace");
    await expect(page.getByRole("article")).toHaveCount(1);
    await shot(page, "console-marketplace");
    await axe(page);
    await page.getByRole("tab", { name: "Trades and prices" }).click();
    await expect(page.getByRole("img", { name: /Price per unit/ })).toBeVisible();
    await page
      .getByRole("tabpanel", { name: "Trades and prices" })
      .getByLabel("Project")
      .selectOption({ label: "Test Mangrove Restoration" });
    await expect(page.getByText("Last price")).toBeVisible();
    await shot(page, "console-marketplace-trades");
    await page.getByRole("tab", { name: "Forward funding" }).click();
    await expect(page.getByRole("note").filter({ hasText: "Buyer risk" })).toBeVisible();
    await page.getByRole("button", { name: /Offers and awards/ }).click();
    await expect(page.getByRole("cell", { name: "500" })).toBeVisible();
    await shot(page, "console-forward-funding");
    await axe(page);
  });
});
