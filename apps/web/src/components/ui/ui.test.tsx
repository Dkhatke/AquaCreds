import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DataTable, DemoBanner, ErrorState, ProvenanceNote, Stat, StatusPill, Tabs } from ".";

describe("StatusPill", () => {
  it("never relies on colour alone", () => {
    render(<StatusPill status="failed">Rejected</StatusPill>);
    const pill = screen.getByText("Rejected").closest("[data-status]");
    expect(pill).toHaveAttribute("data-status", "failed");
    expect(pill).toHaveTextContent("×Rejected");
  });
});

describe("Stat", () => {
  it("shows value with unit and provenance", () => {
    render(
      <dl>
        <Stat
          label="Mangrove"
          value="12.40"
          unit="ha"
          provenance={{ modelVersion: "ecosystem_classifier_v1" }}
        />
      </dl>,
    );
    expect(screen.getByText("12.40")).toHaveTextContent("12.40ha");
    expect(screen.getByText(/Model: ecosystem_classifier_v1/)).toBeInTheDocument();
  });

  it("says 'No data' instead of inventing a zero", () => {
    render(
      <dl>
        <Stat label="Score" value={null} />
      </dl>,
    );
    expect(screen.getByText("No data")).toBeInTheDocument();
  });
});

describe("ProvenanceNote", () => {
  it("joins source, model and date with an honesty label", () => {
    render(<ProvenanceNote source="Sentinel-2 L2A" asOf="14 Dec 2024" label="Context only" />);
    expect(screen.getByText(/Context only\./)).toBeInTheDocument();
    expect(screen.getByText(/Source: Sentinel-2 L2A · As of 14 Dec 2024/)).toBeInTheDocument();
  });
});

describe("Tabs", () => {
  it("supports arrow-key navigation with roving tabindex", async () => {
    const user = userEvent.setup();
    render(
      <Tabs
        label="Index"
        items={[
          { id: "a", label: "NDVI", content: <p>ndvi panel</p> },
          { id: "b", label: "MVI", content: <p>mvi panel</p> },
        ]}
      />,
    );
    const [first, second] = screen.getAllByRole("tab");
    expect(first).toHaveAttribute("aria-selected", "true");
    expect(second).toHaveAttribute("tabindex", "-1");
    first?.focus();
    await user.keyboard("{ArrowRight}");
    expect(second).toHaveAttribute("aria-selected", "true");
    expect(second).toHaveFocus();
    expect(screen.getByText("mvi panel")).toBeVisible();
    await user.keyboard("{Home}");
    expect(first).toHaveFocus();
  });
});

describe("DataTable", () => {
  it("renders an empty message", () => {
    render(
      <DataTable
        caption="Rows"
        columns={[{ key: "x", header: "X", cell: () => null }]}
        rows={[]}
        rowKey={() => "k"}
        empty="Nothing yet"
      />,
    );
    expect(screen.getByText("Nothing yet")).toBeInTheDocument();
  });
});

describe("ErrorState", () => {
  it("shows the error and retries", async () => {
    const onRetry = vi.fn();
    render(<ErrorState error={new Error("API 503: down")} onRetry={onRetry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("API 503: down");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });
});

describe("DemoBanner", () => {
  it("is visibly marked Demo and uses the API banner text", () => {
    render(<DemoBanner text="Synthetic demo data" />);
    expect(screen.getByRole("note", { name: "Demo mode" })).toHaveTextContent(
      "DemoSynthetic demo data",
    );
  });
});
