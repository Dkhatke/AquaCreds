import AxeBuilder from "@axe-core/playwright";
import { type BrowserContext, expect, type Page, test } from "@playwright/test";

const PID = "11111111-1111-4111-8111-111111111111";
const ORG = "22222222-2222-4222-8222-222222222222";
const PERIOD = "33333333-3333-4333-8333-333333333333";
const REPORT = "44444444-4444-4444-8444-444444444444";
const TX = "55555555-5555-4555-8555-555555555555";
const HASH = "ab12".repeat(16);
const COI = "Conflict of interest: verifiers cannot review projects of their own organisation";
const SHOTS = process.env["SCREENSHOT_DIR"];

const project = {
  id: PID,
  org_id: ORG,
  name: "Bhitarkanika Mangrove Restoration",
  description: "",
  state: "Odisha",
  ecosystem: "mangrove",
  methodology: "ccts_bm_fr05_001",
  status: "monitoring",
  tenure_type: "state_forest",
  boundary: { type: "Polygon", coordinates: [] },
  area_ha: 12.5,
  start_date: "2024-01-01",
  planned_mangrove_share_pct: 80,
  soil_disturbance_pct: 5,
  submitted_at: null,
  created_at: "2024-01-01T00:00:00Z",
  documents: [],
};
const period = {
  id: PERIOD,
  project_id: PID,
  kind: "monitoring",
  status: "under_review",
  start_date: "2025-01-01",
  end_date: "2025-12-31",
  created_at: "2025-01-01T00:00:00Z",
};
const report = {
  id: REPORT,
  project_id: PID,
  period_id: PERIOD,
  version: 2,
  status: "under_review",
  report_hash: HASH,
  lccc_units: 143,
  tccc_units: 61,
  period_start_year: 2025,
  period_end_year: 2025,
  generated_at: "2026-03-01T10:00:00Z",
  generated_by: "66666666-6666-4666-8666-666666666666",
  verifier_id: null,
  decided_at: null,
  decision_comment: null,
  submit_tx_id: TX,
  approve_tx_id: null,
  chain_report_id: null,
  anchored_at: null,
  anchor_error: null,
};
const reportV1 = {
  ...report,
  id: "77777777-7777-4777-8777-777777777777",
  version: 1,
  status: "changes_requested",
  decided_at: "2026-02-20T09:00:00Z",
  decision_comment: "Stratum areas do not add up to the project area.",
};
const sha = "cd".repeat(32);
const submissions = [
  {
    id: "88888888-8888-4888-8888-888888888888",
    period_id: PERIOD,
    kind: "plot_centre",
    status: "checked",
    plot_id: "99999999-9999-4999-8999-999999999999",
    sha256: sha,
    lat: 20.72,
    lon: 86.94,
    gps_accuracy_m: 4,
    captured_at: "2025-06-01T06:00:00Z",
    received_at: "2025-06-01T06:01:00Z",
    trust_score: 80,
    exif: null,
    checks: [
      { name: "exif_gps", outcome: "pass", reason: "EXIF GPS within 3 m of app GPS", hard: false },
      {
        name: "capture_nonce",
        outcome: "pass",
        reason: "Captured live within the nonce window",
        hard: false,
      },
      {
        name: "geofence",
        outcome: "warn",
        reason: "9 m outside the boundary, within GPS accuracy",
        hard: false,
      },
      {
        name: "duplicate_visual",
        outcome: "pass",
        reason: "No visually similar photo found",
        hard: false,
      },
      { name: "photo_model", outcome: "skipped", reason: "Photo verifier disabled", hard: false },
    ],
  },
  {
    id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    period_id: PERIOD,
    kind: "general",
    status: "rejected",
    plot_id: null,
    sha256: "ef".repeat(32),
    lat: 20.9,
    lon: 87.1,
    gps_accuracy_m: 9,
    captured_at: "2025-06-02T06:00:00Z",
    received_at: "2025-06-02T06:01:00Z",
    trust_score: 0,
    exif: null,
    checks: [
      {
        name: "capture_nonce",
        outcome: "fail",
        reason: "Nonce already used by another submission",
        hard: true,
      },
    ],
  },
];

async function session(context: BrowserContext, demo = false) {
  const base = { domain: "127.0.0.1", path: "/" };
  await context.addCookies([
    { name: "aq_at", value: "test", ...base },
    ...(demo ? [{ name: "aq_demo", value: "1", ...base }] : []),
  ]);
}

/** Mocked BFF: the Next server is real, every `/bff/api/v1/*` call is answered here. */
async function mockApi(page: Page, platformRole: "user" | "verifier", member = false) {
  const reviewCalls: unknown[] = [];
  await page.route("**/bff/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/bff/api/v1", "");
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/projects") return json([project]);
    if (path === `/projects/${PID}`) return json(project);
    if (path === "/auth/me")
      return json({
        id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        email: "v@example.org",
        full_name: "V",
        platform_role: platformRole,
        created_at: "2024-01-01T00:00:00Z",
        mfa_enabled: false,
        memberships: member ? [{ org_id: ORG, role: "member" }] : [],
      });
    if (path === `/projects/${PID}/periods`) return json([period]);
    if (path === `/periods/${PERIOD}/reports`) return json([report, reportV1]);
    if (path === `/projects/${PID}/plots`)
      return json([
        {
          id: "99999999-9999-4999-8999-999999999999",
          code: "P-01",
          stratum: "fringe",
          lat: 20.72,
          lon: 86.94,
          area_m2: 100,
          marker_id: "M1",
        },
      ]);
    if (path === `/reports/${REPORT}`)
      return json({
        ...report,
        downloads: {
          json_url: "https://files.example/r.json",
          pdf_url: "https://files.example/r.pdf",
          csv_url: "https://files.example/r.zip",
        },
      });
    if (path === `/reports/${REPORT}/verify`)
      return json({
        report_id: REPORT,
        report_hash: HASH,
        stored_hash: HASH,
        stored_matches: true,
        onchain_hash: HASH,
        onchain_source: "receipt",
        onchain_matches: true,
        ok: true,
      });
    if (path === `/chain/transactions/${TX}`)
      return json({
        id: TX,
        chain_id: 80002,
        purpose: "submit_report",
        status: "confirmed",
        tx_hash: `0x${"9f".repeat(32)}`,
        block_number: 1234,
        error: null,
      });
    if (path === `/reports/${REPORT}/review`) {
      reviewCalls.push(route.request().postDataJSON());
      return json({ detail: COI }, 403);
    }
    if (path === `/periods/${PERIOD}/submissions`) return json(submissions);
    return json({ detail: `unmocked ${path}` }, 404);
  });
  return reviewCalls;
}

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
}

async function expectNoAxe(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .analyze();
  expect(
    results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`),
  ).toEqual([]);
}

test.describe("MRV reports", () => {
  test("timeline lists report versions with status pills", async ({ page, context }) => {
    await session(context);
    await mockApi(page, "user");
    await page.goto(`/console/projects/${PID}/reports`);
    await expect(page.getByRole("heading", { level: 1, name: project.name })).toBeVisible();
    await expect(page.getByRole("note").first()).toContainText(
      "Estimated — pending accredited verification; testnet registry units",
    );
    await expect(page.getByRole("link", { name: "Version 2" })).toBeVisible();
    await expect(page.getByText("Changes requested").first()).toBeVisible();
    // Not an org admin: no write action.
    await expect(page.getByRole("button", { name: "Build report" })).toHaveCount(0);
    await shot(page, "mrv-reports-timeline");
    await expectNoAxe(page);
  });

  test("detail shows numbers, verifies the hash and surfaces the conflict-of-interest 403", async ({
    page,
    context,
  }) => {
    await session(context);
    const reviews = await mockApi(page, "verifier", true);
    await page.goto(`/console/reports/${REPORT}`);
    await expect(page.getByRole("heading", { level: 1, name: /Version 2/ })).toBeVisible();
    await expect(page.getByText("143")).toBeVisible();
    await expect(page.getByRole("link", { name: /PDF report/ })).toHaveAttribute(
      "href",
      "https://files.example/r.pdf",
    );
    const tx = page.getByRole("link", { name: /0x9f9f/ });
    await expect(tx).toHaveAttribute("href", /amoy\.polygonscan\.com\/tx\/0x9f/);
    await page.getByRole("button", { name: "Verify" }).click();
    await expect(page.getByText("Stored record matches")).toBeVisible();
    await expect(page.getByText("On-chain hash matches")).toBeVisible();
    await expect(page.getByText("Stratum areas do not add up")).toBeVisible();
    await shot(page, "mrv-report-detail");

    await page.getByRole("radio", { name: "Reject" }).check();
    await page.getByRole("button", { name: "Reject" }).click();
    await expect(page.getByText(/comment is required/i)).toBeVisible();
    await page.getByLabel(/Comment/).fill("Not independent");
    await page.getByRole("button", { name: "Reject" }).click();
    await expect(page.getByRole("alert").filter({ hasText: COI })).toBeVisible();
    expect(reviews).toEqual([{ decision: "reject", comment: "Not independent" }]);
    await shot(page, "mrv-report-conflict-of-interest");
    await expectNoAxe(page);
  });

  test("demo sessions hide write actions", async ({ page, context }) => {
    await session(context, true);
    await mockApi(page, "verifier");
    await page.goto(`/console/reports/${REPORT}`);
    await expect(page.getByRole("heading", { level: 1, name: /Version 2/ })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Verifier decision" })).toHaveCount(0);
    await expect(page.getByText(/Demo data is read-only/)).toBeVisible();
  });
});

test.describe("AI verification", () => {
  test("lists checks with explanations and filters submissions", async ({ page, context }) => {
    await session(context);
    await mockApi(page, "user");
    await page.goto(`/console/projects/${PID}/verification`);
    await expect(page.getByRole("heading", { level: 1, name: project.name })).toBeVisible();
    await expect(page.getByRole("note")).toContainText(
      "never approve or reject a report on their own",
    );
    await expect(page.getByText("9 m outside the boundary, within GPS accuracy")).toBeVisible();
    await expect(page.getByText("Showing 2 of 2")).toBeVisible();
    await shot(page, "ai-verification");

    await page.getByLabel("Worst result").selectOption("fail");
    await expect(page.getByText("Showing 1 of 2")).toBeVisible();
    await expect(page.getByText("Nonce already used by another submission")).toBeVisible();
    await expect(page.getByText("9 m outside the boundary")).toHaveCount(0);
    await expect(page.getByRole("table")).toBeVisible();
    await expectNoAxe(page);
  });

  test("shows an error with retry when submissions fail to load", async ({ page, context }) => {
    await session(context);
    await mockApi(page, "user");
    await page.route(`**/bff/api/v1/periods/${PERIOD}/submissions`, (r) =>
      r.fulfill({ status: 500, contentType: "application/json", body: '{"detail":"boom"}' }),
    );
    await page.goto(`/console/projects/${PID}/verification`);
    await expect(
      page.getByRole("alert").filter({ hasText: "Submissions could not be loaded" }),
    ).toBeVisible();
  });
});
