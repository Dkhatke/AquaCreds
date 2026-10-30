// Browser half of the web smoke test (run by smoke-prod-local.sh through apps/web's Playwright).
//   node infra/scripts/smoke-web-browser.mjs <web-base-url> <email> <password>
// Loads the landing page in headless Chromium, fails on any CSP violation / console error, then logs
// in through the BFF form and checks that the console shell renders. Prints one line per check.
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(fileURLToPath(new URL("../../apps/web/package.json", import.meta.url)));
const { chromium } = require("@playwright/test");
const [base, email, password] = process.argv.slice(2);
if (!base || !email || !password) throw new Error("usage: smoke-web-browser.mjs <url> <email> <password>");

const problems = [];
const browser = await chromium.launch();
const context = await browser.newContext({ ignoreHTTPSErrors: true });
const page = await context.newPage();
page.on("console", (m) => {
  const t = m.text();
  if (m.type() === "error" || /content security policy|refused to/i.test(t)) problems.push(`console: ${t}`);
});
page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
// Next aborts in-flight RSC prefetches on navigation: not a failure.
page.on("requestfailed", (r) => {
  const err = r.failure()?.errorText ?? "";
  if (err.includes("ERR_ABORTED")) return;
  problems.push(`requestfailed: ${r.url()} ${err}`);
});
await page.addInitScript(() => {
  window.__csp = [];
  document.addEventListener("securitypolicyviolation", (e) =>
    window.__csp.push(`${e.violatedDirective} blocked ${e.blockedURI} (${e.sourceFile}:${e.lineNumber} ${e.sample})`),
  );
});

await page.goto(base + "/", { waitUntil: "networkidle" });
const h1 = await page.getByRole("heading", { level: 1 }).first().innerText();
if (!/verifiable carbon evidence/i.test(h1)) throw new Error(`landing h1 unexpected: ${h1}`);
console.log(`landing renders: "${h1}"`);
const csp = await page.evaluate(() => window.__csp);
if (csp.length) problems.push(...csp.map((c) => `CSP violation: ${c}`));
console.log(`CSP violations on landing: ${csp.length}`);

await page.goto(base + "/console", { waitUntil: "load" });
if (!/\/console\/login/.test(page.url())) throw new Error(`/console did not redirect to login: ${page.url()}`);
console.log(`/console -> ${new URL(page.url()).pathname}`);

await page.getByLabel(/email/i).fill(email);
await page.getByLabel(/password/i).fill(password);
await page.getByRole("button", { name: /sign in|log in/i }).click();
await page.waitForURL((u) => !u.pathname.startsWith("/console/login"), { timeout: 15000 });
const cookies = await context.cookies();
const at = cookies.find((c) => c.name === "aq_at");
if (!at || !at.httpOnly || !at.secure) throw new Error("aq_at cookie missing or not httpOnly+Secure");
console.log(`login via BFF OK -> ${new URL(page.url()).pathname}; aq_at httpOnly+Secure`);
await page.waitForLoadState("networkidle");
const csp2 = await page.evaluate(() => window.__csp);
if (csp2.length) problems.push(...csp2.map((c) => `CSP violation (console): ${c}`));

await browser.close();
if (problems.length) {
  console.error(problems.join("\n"));
  process.exit(1);
}
console.log("browser checks passed, no CSP violations or console errors");
