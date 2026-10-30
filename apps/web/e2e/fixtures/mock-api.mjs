// TEST FIXTURE ONLY. A tiny stand-in for the AquaCreds API so the console can be exercised
// end to end (Playwright, screenshots) without Postgres. Every record is obviously synthetic
// ("Fixture ..."); none of it is, or may be presented as, real project data.
import { createServer } from "node:http";

const PORT = Number(process.env.MOCK_API_PORT ?? 8793);
const ORG = "00000000-0000-4000-8000-000000000001";
const ME = "00000000-0000-4000-8000-0000000000aa";
const P1 = "00000000-0000-4000-8000-0000000000b1";
const P2 = "00000000-0000-4000-8000-0000000000b2";
const PER1 = "00000000-0000-4000-8000-0000000000c1";
const PER2 = "00000000-0000-4000-8000-0000000000c2";
const NOW = "2026-10-01T08:00:00Z";

const square = (w, s, e, n) => ({
  type: "Polygon",
  coordinates: [
    [
      [w, s],
      [e, s],
      [e, n],
      [w, n],
      [w, s],
    ],
  ],
});
const project = (id, name, status, geom, ha) => ({
  id,
  org_id: ORG,
  name,
  description: "Fixture project",
  ecosystem: "mangrove",
  methodology: "ccts_bm_fr05_001",
  state: "Odisha",
  status,
  area_ha: ha,
  boundary: geom,
  start_date: "2024-01-15",
  created_at: NOW,
  submitted_at: NOW,
  documents: [],
  planned_mangrove_share_pct: 80,
  soil_disturbance_pct: 5,
  tenure_type: "community",
});
const projects = [
  project(
    P1,
    "Fixture Plot A, Bhitarkanika",
    "monitoring",
    square(86.88, 20.7, 86.92, 20.73),
    42.5,
  ),
  project(
    P2,
    "Fixture Plot B, Mahanadi delta",
    "changes_requested",
    square(86.95, 20.62, 86.99, 20.65),
    18.2,
  ),
];

const state = {
  mfa: {
    enabled: true,
    enabled_at: "2026-09-01T10:00:00Z",
    recovery_codes_remaining: 2,
    required: false,
  },
  notifications: [
    [
      "n1",
      "changes_requested",
      "medium",
      "Changes requested on Fixture Plot B",
      "A verifier asked for changes to the monitoring report.",
      "/console/reports",
      null,
    ],
    [
      "n2",
      "alert_raised",
      "high",
      "Vegetation-loss alert on Fixture Plot A",
      "NDVI fell below the expected range on 2026-09-18.",
      "/console/projects/" + P1 + "/monitoring",
      null,
    ],
    [
      "n3",
      "mfa_recovery_codes_low",
      null,
      "Recovery codes running low",
      "You have 2 unused recovery codes left.",
      "/console/account",
      null,
    ],
    [
      "n4",
      "report_decided",
      null,
      "Report approved",
      "Fixture report v1 was approved.",
      null,
      "2026-09-20T10:00:00Z",
    ],
  ].map(([id, kind, severity, title, body, link, read_at], i) => ({
    id: `00000000-0000-4000-8000-00000000d${i}00`.slice(0, 36),
    kind,
    severity,
    org_id: ORG,
    title,
    body,
    entity_type: null,
    entity_id: null,
    link,
    created_at: `2026-09-${28 - i}T09:00:00Z`,
    read_at,
    emailed_at: null,
  })),
  prefs: {
    report_submitted_for_review: [true, true, false],
    report_decided: [true, true, false],
    changes_requested: [true, true, false],
    alert_raised: [true, true, false],
    submission_rejected: [false, false, false],
    project_submitted: [true, true, false],
    project_registered: [true, true, false],
    credits_issued: [true, true, false],
    forward_fund_offer_status: [false, false, false],
    mfa_recovery_codes_low: [true, true, false],
  },
};
const prefsOut = () => ({
  email_enabled: false,
  preferences: Object.entries(state.prefs).map(([kind, [email, def, customised]]) => ({
    kind,
    email,
    default: def,
    customised,
    note: kind === "alert_raised" ? "Only high-severity alerts are emailed" : null,
  })),
});
const unread = () => state.notifications.filter((n) => n.read_at === null).length;

const sub = (i, status, trust) => ({
  id: `00000000-0000-4000-8000-00000000e${i}00`.slice(0, 36),
  period_id: PER1,
  kind: "plot_centre",
  status,
  plot_id: null,
  sha256: "a".repeat(64),
  lat: 20.71,
  lon: 86.9,
  gps_accuracy_m: 4,
  captured_at: NOW,
  received_at: NOW,
  trust_score: trust,
  checks: [],
  exif: null,
});
const submissions = {
  [PER1]: [
    sub(1, "checked", 92),
    sub(2, "checked", 85),
    sub(3, "checked", 77),
    sub(4, "rejected", 31),
    sub(5, "checked", 96),
    sub(6, "pending_checks", null),
  ],
  [PER2]: [sub(7, "checked", 64)],
};
const period = (id, project_id, kind, s, e, status) => ({
  id,
  project_id,
  kind,
  status,
  start_date: s,
  end_date: e,
  created_at: NOW,
});
const periods = {
  [P1]: [period(PER1, P1, "monitoring", "2026-01-01", "2026-06-30", "approved")],
  [P2]: [period(PER2, P2, "baseline", "2025-01-01", "2025-12-31", "changes_requested")],
};
const report = (id, period_id, project_id, status) => ({
  id,
  period_id,
  project_id,
  status,
  version: 1,
  generated_at: NOW,
  generated_by: ME,
  report_hash: "b".repeat(64),
  lccc_units: 0,
  tccc_units: 0,
  period_start_year: 2026,
  period_end_year: 2026,
  anchor_error: null,
  anchored_at: null,
  approve_tx_id: null,
  chain_report_id: null,
  decided_at: null,
  decision_comment: null,
  submit_tx_id: null,
  verifier_id: null,
});
const reports = {
  [PER1]: [report("r1", PER1, P1, "approved")],
  [PER2]: [report("r2", PER2, P2, "changes_requested")],
};

const component = (name, weight, score) => ({
  name,
  weight,
  score,
  insufficient_data: score === null,
  sub_scores: {},
  inputs: {},
  notes: [],
});
const projStew = (id, ha) => ({
  project_id: id,
  area_ha: ha,
  score: 71.4,
  insufficient_data: false,
  renormalised: false,
  score_version: "stewardship-v2",
  computed_at: NOW,
  notes: [],
  components: [
    component("verification_continuity", 0.4, 63.2),
    component("satellite_survival", 0.35, 80),
    component("evidence_quality", 0.25, 68.5),
  ],
});

const json = (res, code, body) => {
  res.writeHead(code, { "content-type": "application/json" });
  res.end(body === undefined ? "" : JSON.stringify(body));
};
const body = (req) =>
  new Promise((r) => {
    let b = "";
    req.on("data", (c) => (b += c));
    req.on("end", () => r(b ? JSON.parse(b) : {}));
  });

createServer(async (req, res) => {
  const url = new URL(req.url, "http://x");
  const p = url.pathname.replace(/^\/api\/v1/, "");
  const m = req.method;
  const gProj = /^\/projects\/([^/]+)\/(classifications|alerts|periods|timeseries)$/.exec(p);
  const gPer = /^\/periods\/([^/]+)\/(reports|submissions)$/.exec(p);
  if (m === "GET" && p === "/demo/status")
    return json(res, 200, { enabled: true, banner: "Demo: synthetic fixture data" });
  if (m === "POST" && p === "/demo/session")
    return json(res, 200, {
      access_token: "fixture-token",
      expires_in: 3600,
      demo: true,
      org_id: ORG,
      project_ids: [P1, P2],
      banner: "Demo: synthetic fixture data",
    });
  if (m === "GET" && p === "/auth/me")
    return json(res, 200, {
      id: ME,
      email: "fixture@example.org",
      full_name: "Fixture Admin",
      platform_role: "user",
      mfa_enabled: true,
      created_at: NOW,
      memberships: [{ org_id: ORG, role: "admin" }],
    });
  if (m === "GET" && p === "/auth/mfa") return json(res, 200, state.mfa);
  if (m === "POST" && p === "/auth/mfa/recovery-codes") {
    const b = await body(req);
    if (b.code !== "123456") return json(res, 401, { detail: "Invalid authentication code" });
    state.mfa.recovery_codes_remaining = 10;
    return json(res, 200, {
      recovery_codes: Array.from({ length: 10 }, (_, i) => `FIXT-${String(1000 + i)}-CODE`),
    });
  }
  if (m === "GET" && p === "/orgs")
    return json(res, 200, [
      { id: ORG, name: "Fixture Coastal Trust", slug: "fixture", org_type: "ngo", created_at: NOW },
    ]);
  if (m === "GET" && p === `/orgs/${ORG}/members`)
    return json(res, 200, [
      { user_id: ME, email: "fixture@example.org", full_name: "Fixture Admin", role: "admin" },
      {
        user_id: "u2",
        email: "agent@example.org",
        full_name: "Fixture Field Agent",
        role: "field_agent",
      },
    ]);
  if (m === "GET" && p === `/orgs/${ORG}/stewardship`)
    return json(res, 200, {
      org_id: ORG,
      score: 71.4,
      insufficient_data: false,
      notes: [],
      score_version: "stewardship-v2",
      computed_at: NOW,
      params: {},
      components: { verification_continuity: 63.2, satellite_survival: 80, evidence_quality: 68.5 },
      projects: [projStew(P1, 42.5), projStew(P2, 18.2)],
      forward_funding: {
        eligible: false,
        reasons: ["Needs two approved reports"],
        criteria_version: "forward-funding-v1",
      },
    });
  if (m === "GET" && p === "/projects") return json(res, 200, projects);
  if (gProj && m === "GET") {
    const [, id, what] = gProj;
    if (what === "classifications")
      return json(res, 200, [
        {
          id: "c-" + id,
          project_id: id,
          season_start_year: 2025,
          created_at: NOW,
          model_version: "ecosystem_classifier_v1.1",
          mangrove_ha: id === P1 ? 30.5 : 12.1,
          class_areas_ha: {},
          classes: [],
          low_confidence_share: 0.04,
        },
      ]);
    if (what === "alerts")
      return json(
        res,
        200,
        id === P1
          ? [
              {
                id: "a1",
                project_id: id,
                kind: "vegetation_loss",
                metric: "ndvi",
                observed_on: "2026-09-18",
                observed_value: 0.41,
                expected: {},
                severity: "high",
                message: "NDVI below expected range",
                acknowledged_by: null,
                acknowledged_at: null,
                created_at: NOW,
                run_id: null,
              },
            ]
          : [],
      );
    if (what === "periods") return json(res, 200, periods[id] ?? []);
    return json(res, 200, {
      project_id: id,
      index: "ndvi",
      sensor: "sentinel-2-l2a",
      units: "index",
      note: "",
      min_clear_fraction: 0.5,
      points: [
        {
          date: "2026-09-20",
          acquired_at: NOW,
          item_id: "x",
          mean: 0.62,
          p10: 0.5,
          p50: id === P1 ? 0.64 : 0.58,
          p90: 0.7,
          clear_fraction: 0.9,
          cloud_cover: 5,
        },
      ],
    });
  }
  if (gPer && m === "GET")
    return json(res, 200, (gPer[2] === "reports" ? reports : submissions)[gPer[1]] ?? []);
  if (m === "GET" && p === "/notifications/unread-count")
    return json(res, 200, { unread: unread() });
  if (m === "GET" && p === "/notifications") {
    const only = url.searchParams.get("unread") === "true";
    const items = state.notifications.filter((n) => !only || n.read_at === null);
    return json(res, 200, { items, total: items.length, unread: unread(), limit: 20, offset: 0 });
  }
  if (m === "POST" && p === "/notifications/read-all") {
    let updated = 0;
    for (const n of state.notifications)
      if (!n.read_at) {
        n.read_at = new Date().toISOString();
        updated++;
      }
    return json(res, 200, { updated });
  }
  const gRead = /^\/notifications\/([^/]+)\/read$/.exec(p);
  if (gRead && m === "POST") {
    const n = state.notifications.find((x) => x.id === gRead[1]);
    if (!n) return json(res, 404, { detail: "Notification not found" });
    n.read_at ??= new Date().toISOString();
    return json(res, 200, n);
  }
  if (p === "/notifications/preferences" && m === "GET") return json(res, 200, prefsOut());
  if (p === "/notifications/preferences" && m === "PUT") {
    const b = await body(req);
    for (const [kind, v] of Object.entries(b.email ?? {})) {
      const cur = state.prefs[kind];
      if (!cur) continue;
      if (v === null) {
        cur[0] = cur[1];
        cur[2] = false;
      } else {
        cur[0] = v;
        cur[2] = true;
      }
    }
    return json(res, 200, prefsOut());
  }
  return json(res, 404, { detail: `fixture has no route ${m} ${p}` });
}).listen(PORT, "127.0.0.1", () => console.log(`mock api on ${PORT}`));
