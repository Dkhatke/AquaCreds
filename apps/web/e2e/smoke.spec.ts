import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("landing page renders the hero, flow and attribution", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", {
      level: 1,
      name: "From coastal ecosystems to verifiable carbon evidence.",
    }),
  ).toBeVisible();
  await expect(page.getByText("AI-powered blue carbon MRV")).toBeVisible();
  for (const step of ["Collect", "Analyse", "Verify", "Record"]) {
    await expect(page.getByRole("heading", { level: 3, name: new RegExp(step) })).toBeVisible();
  }
  await expect(
    page.getByText(/Contains modified Copernicus Sentinel data 20\d\d/).first(),
  ).toBeVisible();
  // No API configured: the live registry block is hidden rather than showing made-up numbers.
  await expect(page.getByText("Public registry, live")).toHaveCount(0);
});

test("Discover the technology scrolls to the workflow", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "Discover the technology" }).click();
  await expect(page).toHaveURL(/#how-it-works$/);
  await expect(
    page.getByRole("heading", { name: "Collect, analyse, verify, record." }),
  ).toBeInViewport();
});

test("Explore Demo opens the demo entry without an account", async ({ page }) => {
  await page.goto("/");
  await page
    .getByRole("link", { name: /Explore the demo/ })
    .first()
    .click();
  await expect(page).toHaveURL(/\/console\/demo$/);
  await expect(page.getByRole("heading", { level: 1, name: "Explore the demo" })).toBeVisible();
  await expect(page.getByRole("note", { name: "Demo mode" })).toBeVisible();
  // This server runs with FEATURE_DEMO off, so it must say so instead of failing silently.
  await expect(page.getByRole("heading", { name: "The demo is not available here" })).toBeVisible();
});

test("console pages require a session", async ({ page }) => {
  await page.goto("/console/projects/00000000-0000-0000-0000-000000000000/monitoring");
  await expect(page).toHaveURL(/\/console\/login\?next=/);
  await expect(page.getByRole("heading", { name: "Sign in to the console" })).toBeVisible();
});

for (const path of ["/", "/how-it-works", "/marketplace", "/console/login", "/console/demo"]) {
  test(`no axe violations (WCAG 2.2 AA) on ${path}`, async ({ page }) => {
    await page.goto(path);
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
      .analyze();
    expect(
      results.violations.map(
        (v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`,
      ),
    ).toEqual([]);
  });
}

test("reduced motion stops the flow animation", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/#how-it-works");
  const marker = page.locator(".flow-step-marker").first();
  await expect(marker).toHaveCSS("animation-name", "none");
});
