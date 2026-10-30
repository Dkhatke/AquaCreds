import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const post = vi.fn();
vi.mock("@/lib/api-browser", async (orig) => ({
  ...(await orig<typeof import("@/lib/api-browser")>()),
  browserClient: () => ({ api: { POST: post } }),
}));

import { SubmissionCard } from "../verification/SubmissionCard";
import { ReviewPanel } from "./ReviewPanel";

const wrap = (ui: ReactNode) => (
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    {ui}
  </QueryClientProvider>
);

beforeEach(() => post.mockReset());

describe("ReviewPanel", () => {
  it("requires a comment to reject and does not call the API", async () => {
    const user = userEvent.setup();
    render(wrap(<ReviewPanel reportId="r" projectId="p" periodId="q" belongsToOrg={false} />));
    await user.click(screen.getByRole("radio", { name: "Reject" }));
    await user.click(screen.getByRole("button", { name: "Reject" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/comment is required/i);
    expect(post).not.toHaveBeenCalled();
  });

  it("shows the API's conflict-of-interest message", async () => {
    const user = userEvent.setup();
    const detail =
      "Conflict of interest: verifiers cannot review projects of their own organisation";
    post.mockResolvedValue({ error: { detail }, response: new Response("{}", { status: 403 }) });
    render(wrap(<ReviewPanel reportId="r" projectId="p" periodId="q" belongsToOrg />));
    expect(screen.getByRole("note")).toHaveTextContent(/conflict of interest/i);
    await user.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(detail));
    expect(post).toHaveBeenCalledWith(
      "/api/v1/reports/{report_id}/review",
      expect.objectContaining({ body: { decision: "approve", comment: "" } }),
    );
  });
});

describe("SubmissionCard", () => {
  it("explains each check with a labelled pill and says when no photo is available", () => {
    render(
      <SubmissionCard
        plotCode="P-01"
        sub={
          {
            id: "11111111-1111-1111-1111-111111111111",
            period_id: "p",
            kind: "plot_centre",
            status: "checked",
            plot_id: null,
            sha256: "ab".repeat(32),
            lat: 20.7,
            lon: 86.9,
            gps_accuracy_m: 4,
            captured_at: "2026-02-01T00:00:00Z",
            received_at: "2026-02-01T00:00:00Z",
            trust_score: 60,
            exif: null,
            checks: [
              {
                name: "geofence",
                outcome: "warn",
                reason: "12 m outside the boundary",
                hard: false,
              },
              { name: "photo_model", outcome: "skipped", reason: "Photo verifier disabled" },
            ],
          } as never
        }
      />,
    );
    expect(screen.getByText("Warning").closest("[data-status]")).toHaveAttribute(
      "data-status",
      "review",
    );
    expect(screen.getByText("Not run")).toBeInTheDocument();
    expect(screen.getByText("12 m outside the boundary")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /not previewable/ })).toBeInTheDocument();
    expect(screen.getByText(/never rejects a photo on its own/)).toBeInTheDocument();
  });
});
