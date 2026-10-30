// Minimal mock of the AquaCreds API for the Playwright "mocked" project. Fixtures are test data
// shaped like the real responses; nothing here is shown outside tests.
import http from "node:http";

const PORT = Number(process.env.MOCK_API_PORT ?? 4010);
const P1 = "11111111-1111-4111-8111-111111111111";
const ORG = "22222222-2222-4222-8222-222222222222";
const REPORT = "33333333-3333-4333-8333-333333333333";
const PERIOD = "44444444-4444-4444-8444-444444444444";
const TX1 = "55555555-5555-4555-8555-555555555555";
const HASH = "a".repeat(64);
const TXH = `0x${"b".repeat(64)}`;
const polygon = {
  type: "Polygon",
  coordinates: [
    [
      [86.85, 20.7],
      [86.9, 20.7],
      [86.9, 20.74],
      [86.85, 20.74],
      [86.85, 20.7],
    ],
  ],
};
const project = {
  id: P1,
  org_id: ORG,
  name: "Test Mangrove Restoration",
  description: "Fixture project.",
  state: "Odisha",
  ecosystem: "mangrove",
  methodology: "ccts_bm_fr05_001",
  tenure_type: "forest_land",
  boundary: polygon,
  area_ha: 123.45,
  start_date: "2025-03-01",
  status: "draft",
  submitted_at: null,
  planned_mangrove_share_pct: 95,
  soil_disturbance_pct: 4,
  created_at: "2025-02-01T00:00:00Z",
  documents: [],
};
const summary = {
  id: P1,
  name: project.name,
  state: "Odisha",
  ecosystem: "mangrove",
  methodology: "ccts_bm_fr05_001",
  area_ha: 123.45,
  centroid: [86.875, 20.72],
  org: { name: "Fixture NGO", org_type: "ngo" },
  registered_on_chain: true,
  start_date: "2025-03-01",
  status: "monitoring",
};
const chainRef = {
  block_hash: TXH,
  block_number: 100,
  chain_id: 80002,
  contract_address: `0x${"c".repeat(40)}`,
  explorer_url: `https://amoy.polygonscan.com/tx/${TXH}`,
  log_index: 0,
  tx_hash: TXH,
};
const projectRef = { id: P1, name: project.name, project_key: `0x${"d".repeat(64)}` };
const vintage = {
  token_id: 1,
  credit_type: "temporary",
  credit_unit: "tCCC",
  issued: 1000,
  retired: 100,
  outstanding: 900,
  replaced: 0,
  expired: false,
  expires_at: "2035-01-01T00:00:00Z",
  report_id: 1,
  project: projectRef,
  issuances: [
    {
      amount: 1000,
      block_number: 100,
      block_time: "2025-06-01T00:00:00Z",
      explorer_url: chainRef.explorer_url,
      tx_hash: TXH,
    },
  ],
};
const listing = (id, available) => ({
  listing_id: id,
  amount_listed: 500,
  amount_sold: available ? 100 : 500,
  remaining: available ? 400 : 0,
  available,
  unavailable_reason: available ? null : "sold_out",
  status: available ? "active" : "sold_out",
  listed: chainRef,
  closed: null,
  listed_at: "2025-06-02T00:00:00Z",
  payment_token: { address: `0x${"e".repeat(40)}`, decimals: 6 },
  price_per_unit: { decimal: "12.500000", units: 12500000 },
  project: projectRef,
  project_facts: summary,
  stewardship: { score: 72.4, score_version: "stewardship_v1", insufficient_data: false },
  vintage: {
    token_id: 1,
    credit_type: "temporary",
    credit_unit: "tCCC",
    expired: false,
    expires_at: "2035-01-01T00:00:00Z",
    mrv_report_id: REPORT,
    report_hash: HASH,
    report_id: 1,
  },
});
const trade = {
  amount: 100,
  block_time: "2025-06-03T00:00:00Z",
  chain: chainRef,
  credit_type: "temporary",
  fee: { decimal: "0.250000", units: 250000 },
  listing_id: 1,
  payment_token: { address: null, decimals: 6 },
  price_per_unit: { decimal: "12.500000", units: 12500000 },
  project: projectRef,
  token_id: 1,
  total_price: { decimal: "1250.000000", units: 1250000000 },
};
const page = (items) => ({ items, total: items.length, limit: 20, offset: 0 });
const retirement = {
  retirement_id: 7,
  amount: 50,
  beneficiary: `0x${"f".repeat(40)}`,
  block_number: 120,
  block_time: "2025-07-01T00:00:00Z",
  credit_type: "temporary",
  indexed_at: "2025-07-01T00:00:00Z",
  project: projectRef,
  purpose: "Fixture retirement",
  token_id: 1,
  tx_hash: TXH,
};
const upfront = {
  amount_paid: 0,
  bps: 2000,
  buyer_risk: true,
  note: "Upfront is released before delivery.",
  refundable: false,
  share_pct: 20,
};
const request = {
  request_id: 1,
  status: "open",
  quantity: 1000,
  max_price: 15,
  active_offers: 1,
  awarded_quantity: 0,
  awarded_value: 0,
  buyer: `0x${"1".repeat(40)}`,
  delivered_quantity: 0,
  delivery_deadline: 1900000000,
  escrow_deposited: 0,
  escrow_left: 0,
  offer_window_end: 1800000000,
  refundable_after_deadline: 0,
  refunded_undelivered: 0,
  released: 0,
  tx_hash: TXH,
  unused_escrow_refunded: 0,
  upfront,
};
const publicReport = {
  id: REPORT,
  period_id: PERIOD,
  project_id: P1,
  version: 1,
  status: "approved",
  period_kind: "monitoring",
  period_start_year: 2025,
  period_end_year: 2025,
  report_hash: HASH,
  tccc_units: 1000,
  lccc_units: 0,
  generated_at: "2025-05-01T00:00:00Z",
  decided_at: "2025-05-20T00:00:00Z",
  downloads: { pdf_url: "x", record_url: "y" },
  anchoring: {
    approve_tx: null,
    chain_report_id: 1,
    completed_at: null,
    hash_anchored: true,
    submit_tx: {
      block_number: 100,
      chain_id: 80002,
      explorer_url: chainRef.explorer_url,
      tx_hash: TXH,
    },
  },
};
const publicDetail = {
  ...summary,
  description: "Fixture project.",
  boundary: { geometry: polygon, simplification: "Douglas-Peucker", tolerance_deg: 0.0001 },
  alerts: { high: 0, medium: 1, low: 2, total: 3, latest_observed_on: "2025-04-01" },
  evidence: {
    checked: 8,
    median_trust_score: 0.82,
    pending_checks: 1,
    rejected: 1,
    submissions_total: 10,
  },
  mrv_reports: [publicReport],
  periods: [
    {
      id: PERIOD,
      kind: "monitoring",
      start_date: "2025-01-01",
      end_date: "2025-12-31",
      status: "open",
    },
  ],
  registration: {
    block_hash: TXH,
    block_number: 90,
    boundary_hash: HASH,
    chain_id: 80002,
    contract_address: chainRef.contract_address,
    explorer_url: chainRef.explorer_url,
    log_index: 0,
    start_year: 2025,
    tx_hash: TXH,
  },
  reports: [],
  satellite: {
    index: "ndvi",
    last_12_months: [],
    latest_clear_fraction: 0.8,
    latest_date: "2025-04-01",
    latest_p50: 0.61,
    min_clear_fraction: 0.5,
    trend_p50_per_year: 0.02,
  },
  vintages: [vintage],
};
const chainTx = (id, purpose, status, extra = {}) => ({
  id,
  purpose,
  status,
  args: [],
  attempts: 1,
  block_number: status === "confirmed" ? 100 : null,
  chain_id: 80002,
  confirmed_at: null,
  contract: "Registry",
  created_at: "2025-05-01T00:00:00Z",
  entity_id: P1,
  entity_type: "project",
  error: null,
  error_name: null,
  from_address: `0x${"1".repeat(40)}`,
  function: "registerProject",
  gas_used: null,
  nonce: 1,
  result: {},
  signer: "registrar",
  submitted_at: null,
  to_address: `0x${"2".repeat(40)}`,
  tx_hash: status === "queued" || status === "waiting_for_gas" ? null : TXH,
  ...extra,
});

function route(method, url, body) {
  const p = url.pathname;
  const q = url.searchParams;
  if (p === "/api/v1/public/projects") return page([summary]);
  if (p === `/api/v1/public/projects/${P1}`) return publicDetail;
  if (p === "/api/v1/public/vintages") return page([vintage]);
  if (p === "/api/v1/public/retirements") return page([retirement]);
  if (p === "/api/v1/public/retirements/7") {
    return {
      ...retirement,
      certificate_pdf_url: "x",
      qr_png_url: "x",
      qr_svg_url: "x",
      verification: {
        certificate_url: "x",
        chain: chainRef,
        contract: chainRef.contract_address,
        event: "Retired",
        fields: {},
        payload_sha256: HASH,
      },
    };
  }
  if (p === "/api/v1/public/verify-hash") {
    const h = method === "POST" ? JSON.parse(body || "{}").sha256_hex : q.get("sha256_hex");
    const clean = String(h).replace(/^0x/, "");
    return clean === HASH
      ? {
          found: true,
          sha256_hex: clean,
          matches: [
            {
              anchored: true,
              chain: chainRef,
              mrv_report_id: REPORT,
              period_start: 2025,
              period_end: 2025,
              project: projectRef,
              report_id: 1,
              source: "chain",
              status: "approved",
            },
          ],
        }
      : { found: false, sha256_hex: clean, matches: [] };
  }
  if (p === "/api/v1/public/marketplace/listings") {
    return page(
      q.get("include_unavailable") === "true"
        ? [listing(1, true), listing(2, false)]
        : [listing(1, true)],
    );
  }
  if (p === "/api/v1/public/marketplace/trades") return page([trade]);
  if (p === `/api/v1/public/marketplace/projects/${P1}/market`) {
    return {
      project: projectRef,
      active_listings: [listing(1, true)],
      last_price: trade.price_per_unit,
      payment_token: trade.payment_token,
      volume_units: 100,
      trades: [trade],
      price_history: [
        {
          amount: 100,
          block_number: 110,
          credit_type: "temporary",
          price_per_unit: { decimal: "11.000000", units: 11000000 },
          time: "2025-06-01T00:00:00Z",
          token_id: 1,
        },
        {
          amount: 100,
          block_number: 111,
          credit_type: "temporary",
          price_per_unit: trade.price_per_unit,
          time: "2025-06-03T00:00:00Z",
          token_id: 1,
        },
      ],
    };
  }
  if (p === "/api/v1/auth/me") {
    return {
      id: "99999999-9999-4999-8999-999999999999",
      email: "admin@example.test",
      full_name: "Test Admin",
      platform_role: "user",
      mfa_enabled: false,
      created_at: "2025-01-01T00:00:00Z",
      memberships: [{ org_id: ORG, role: "admin" }],
    };
  }
  if (p === "/api/v1/projects") return [project];
  if (p === `/api/v1/projects/${P1}`) return project;
  if (p === `/api/v1/projects/${P1}/submit` && method === "POST") {
    return {
      status: 422,
      body: {
        detail: {
          code: "submission_blocked",
          issues: [
            {
              code: "missing_document",
              message: "Required tenure document missing: forest_dept_agreement",
            },
            { code: "boundary_overlap", message: "Overlaps Other Project by 3.2 ha" },
          ],
        },
      },
    };
  }
  if (p === "/api/v1/chain/status") {
    return {
      chain_id: 80002,
      expected_chain_id: 80002,
      configured: true,
      connected: true,
      contracts: { registry: "0x1" },
      error: null,
      indexer_cursors: {},
      latest_block: 12345,
      signers: [
        { address: `0x${"1".repeat(40)}`, balance_wei: 2500000000000000000, role: "registrar" },
      ],
    };
  }
  if (p === `/api/v1/projects/${P1}/chain`) {
    return {
      project_id: P1,
      project_key: projectRef.project_key,
      registered: true,
      events: [],
      transactions: [
        chainTx(TX1, "register_project", "confirmed"),
        chainTx("66666666-6666-4666-8666-666666666666", "submit_report", "waiting_for_gas", {
          signer: "org_wallet",
        }),
        chainTx("77777777-7777-4777-8777-777777777777", "gas_top_up", "submitted", {
          signer: "funder",
        }),
      ],
    };
  }
  if (p === `/api/v1/projects/${P1}/periods`) return [{ id: PERIOD }];
  if (p === `/api/v1/periods/${PERIOD}/reports`) {
    return [
      {
        id: REPORT,
        period_id: PERIOD,
        project_id: P1,
        version: 1,
        status: "approved",
        report_hash: HASH,
        tccc_units: 1000,
        lccc_units: 0,
        period_start_year: 2025,
        period_end_year: 2025,
        generated_at: "2025-05-01T00:00:00Z",
        generated_by: "x",
        decided_at: null,
        decision_comment: null,
        anchor_error: null,
        anchored_at: null,
        approve_tx_id: null,
        chain_report_id: 1,
        submit_tx_id: TX1,
        verifier_id: null,
      },
    ];
  }
  if (p === "/api/v1/forward-fund/requests")
    return { disclaimer: "Fixture disclaimer from the API.", items: [request] };
  if (p === "/api/v1/forward-fund/requests/1") {
    return {
      ...request,
      disclaimer: "x",
      awards: [],
      offers: [
        {
          offer_id: 1,
          price_per_unit: 12,
          project_id: P1,
          project_key: projectRef.project_key,
          quantity: 500,
          request_id: 1,
          seller: `0x${"3".repeat(40)}`,
          status: "active",
          tx_hash: TXH,
          withdrawn_tx_hash: null,
        },
      ],
      onchain: null,
    };
  }
  return null;
}

http
  .createServer((req, res) => {
    const url = new URL(req.url ?? "/", `http://${req.headers.host}`);
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      const body = Buffer.concat(chunks).toString();
      if (url.pathname.endsWith("/qr.png")) {
        res.writeHead(200, { "content-type": "image/png" });
        res.end(
          Buffer.from(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
            "base64",
          ),
        );
        return;
      }
      const out = route(req.method ?? "GET", url, body);
      if (out === null) {
        res.writeHead(404, { "content-type": "application/json" });
        res.end(JSON.stringify({ detail: "Not found" }));
        return;
      }
      const status =
        out && typeof out === "object" && "status" in out && "body" in out ? out.status : 200;
      const payload = status === 200 ? out : out.body;
      res.writeHead(status, { "content-type": "application/json" });
      res.end(JSON.stringify(payload));
    });
  })
  .listen(PORT, "127.0.0.1", () => console.log(`mock api on ${PORT}`));
