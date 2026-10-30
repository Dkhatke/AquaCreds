"use client";

import { useState } from "react";
import { Button, Card, ErrorState, LoadingState, StatusPill } from "@/components/ui";
import { errorMessage } from "@/lib/api-browser";
import { verificationSummary } from "@/lib/mrv";
import { useVerifyReport } from "@/lib/queries";

function Hash({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs uppercase tracking-wide text-fg-muted">{label}</dt>
      <dd className="tabular break-all font-mono text-xs">{value ?? "—"}</dd>
    </div>
  );
}

/** Report hash with an explicit Verify action: database vs stored record vs on-chain. */
export function VerifyPanel({ reportId, reportHash }: { reportId: string; reportHash: string }) {
  const [asked, setAsked] = useState(false);
  const q = useVerifyReport(reportId, asked);
  const summary = q.data ? verificationSummary(q.data) : null;
  return (
    <Card
      title="Report hash"
      meta="SHA-256 of the canonical report record (RFC 8785 JSON)"
      actions={
        <Button
          variant="secondary"
          size="sm"
          disabled={q.isFetching}
          onClick={() => (asked ? void q.refetch() : setAsked(true))}
        >
          {q.isFetching ? "Verifying…" : "Verify"}
        </Button>
      }
    >
      <dl className="space-y-3">
        <Hash label="Recorded in the database" value={reportHash} />
        {q.data && (
          <>
            <Hash label="Recomputed from the stored record" value={q.data.stored_hash} />
            <Hash
              label={`On-chain (${q.data.onchain_source ?? "not anchored"})`}
              value={q.data.onchain_hash}
            />
          </>
        )}
      </dl>
      <div aria-live="polite" className="mt-3 space-y-2">
        {q.isFetching && <LoadingState label="Recomputing the hash" />}
        {q.isError && (
          <ErrorState title="Verification could not run" error={errorMessage(q.error)} />
        )}
        {q.data && summary && !q.isFetching && (
          <div className="flex flex-wrap gap-2">
            <StatusPill status={summary.stored.status}>{summary.stored.label}</StatusPill>
            <StatusPill status={summary.onchain.status}>{summary.onchain.label}</StatusPill>
            <StatusPill status={q.data.ok ? "ok" : "failed"}>
              {q.data.ok ? "Consistent" : "Inconsistent"}
            </StatusPill>
          </div>
        )}
      </div>
    </Card>
  );
}
