import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { WalletGate } from "@/components/registry/WalletGate";
import type { ListingRow } from "@/lib/registry-queries";
import { BuyerRiskNotice } from "./ForwardFunding";
import { ListingCard } from "./ListingCard";
import { PriceChart } from "./PriceChart";

const base = {
  listing_id: 3,
  amount_listed: 500,
  amount_sold: 100,
  remaining: 400,
  available: true,
  unavailable_reason: null,
  status: "active",
  listed: {
    block_hash: "0x1",
    block_number: 1,
    chain_id: 31337,
    contract_address: "0x2",
    explorer_url: "https://example.test/tx/1",
    log_index: 0,
    tx_hash: `0x${"a".repeat(64)}`,
  },
  closed: null,
  listed_at: null,
  payment_token: { address: null, decimals: 6 },
  price_per_unit: { decimal: "12.500000", units: 12500000 },
  project: { id: "p1", name: "Fixture", project_key: "0xkey" },
  project_facts: null,
  stewardship: { score: 72.44, score_version: "stewardship_v1", insufficient_data: false },
  vintage: {
    token_id: 1,
    credit_type: "temporary",
    credit_unit: "tCCC",
    expired: false,
    expires_at: "2035-01-01T00:00:00Z",
    mrv_report_id: null,
    report_hash: "b".repeat(64),
    report_id: 1,
  },
} as unknown as ListingRow;

describe("ListingCard", () => {
  it("shows the MRV report hash, stewardship score and version, and honesty labels", () => {
    render(<ListingCard listing={base} />);
    expect(screen.getByText("72.4")).toBeInTheDocument();
    expect(screen.getByText(/stewardship_v1/)).toBeInTheDocument();
    expect(screen.getByTitle("b".repeat(64))).toBeInTheDocument();
    expect(screen.getByText(/estimated, pending accredited verification/i)).toBeInTheDocument();
  });
  it("shows a local chain as local, never as an explorer link", () => {
    render(<ListingCard listing={base} />);
    expect(screen.getByText("Local chain 31337")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Explorer/ })).toBeNull();
  });
  it("labels unavailable listings with the reason", () => {
    render(<ListingCard listing={{ ...base, available: false, unavailable_reason: "expired" }} />);
    expect(screen.getByText("Vintage expired")).toBeInTheDocument();
  });
  it("says so when stewardship has insufficient data instead of inventing a score", () => {
    render(
      <ListingCard
        listing={{
          ...base,
          stewardship: { score: null, score_version: "stewardship_v1", insufficient_data: true },
        }}
      />,
    );
    expect(screen.getByText(/Insufficient data/)).toBeInTheDocument();
  });
});

describe("wallet gate", () => {
  it("is a disabled, honest placeholder", () => {
    render(<WalletGate action="Buying" />);
    expect(screen.getByRole("button", { name: "Connect wallet (coming soon)" })).toBeDisabled();
    expect(screen.getByText(/No transaction has been or will be made/)).toBeInTheDocument();
  });
});

describe("forward funding disclaimer", () => {
  it("states the buyer risk", () => {
    render(<BuyerRiskNotice apiText="API says hi" />);
    expect(screen.getByRole("note")).toHaveTextContent(/at the buyer's risk/);
    expect(screen.getByText(/API says hi/)).toBeInTheDocument();
  });
});

describe("PriceChart", () => {
  it("renders a titled chart and a data table", () => {
    render(
      <PriceChart
        label="Price per unit"
        points={[
          { time: "2025-06-01T00:00:00Z", value: 11, amount: 10 },
          { time: "2025-06-03T00:00:00Z", value: 12.5, amount: 20 },
        ]}
      />,
    );
    expect(screen.getByRole("img", { name: /2 trades/ })).toBeInTheDocument();
  });
});
