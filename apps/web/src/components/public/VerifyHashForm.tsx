"use client";

import { unwrap } from "@aquacreds/api-client";
import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { TxLink } from "@/components/chain/TxLink";
import { Field, inputClass, ProblemList } from "@/components/registry/fields";
import { Button, Card, StatusPill } from "@/components/ui";
import { browserClient } from "@/lib/api-browser";
import { problemMessages } from "@/lib/api-problems";
import { explorerUrlFor } from "@/lib/chain-links";
import { formatNumber } from "@/lib/format";
import { PublicProviders } from "./PublicProviders";

const HEX = /^(0x)?[0-9a-fA-F]{64}$/;

function Form({ initial }: { initial: string }) {
  const verify = useMutation({
    mutationFn: (hash: string) =>
      unwrap(
        browserClient().api.POST("/api/v1/public/verify-hash", { body: { sha256_hex: hash } }),
      ),
  });
  const input = useRef<HTMLInputElement>(null);
  const [localError, setLocalError] = useState<string | null>(null);

  const run = (value: string) => {
    const v = value.trim();
    if (!HEX.test(v)) {
      setLocalError(
        "Enter a SHA-256 hash: 64 hexadecimal characters, optionally starting with 0x.",
      );
      verify.reset();
      return;
    }
    setLocalError(null);
    verify.mutate(v);
  };

  // biome-ignore lint/correctness/useExhaustiveDependencies: run once for a prefilled ?hash=
  useEffect(() => {
    if (initial) run(initial);
  }, []);

  const result = verify.data;
  return (
    <div className="space-y-6">
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          run(input.current?.value ?? "");
        }}
      >
        <Field
          label="Report hash (SHA-256)"
          className="min-w-72 flex-1"
          hint="Paste the hash printed on an MRV report or shown on a listing."
        >
          {({ id, describedBy }) => (
            <input
              id={id}
              ref={input}
              defaultValue={initial}
              aria-describedby={describedBy}
              spellCheck={false}
              autoComplete="off"
              className={`${inputClass} font-mono text-xs`}
            />
          )}
        </Field>
        <Button type="submit" disabled={verify.isPending}>
          {verify.isPending ? "Checking…" : "Verify"}
        </Button>
      </form>
      {localError && <ProblemList title="Check the hash" messages={[localError]} />}
      {verify.isError && (
        <ProblemList title="Verification failed" messages={problemMessages(verify.error)} />
      )}
      {result && (
        <div aria-live="polite">
          {result.found ? (
            <div className="space-y-3">
              <StatusPill status="ok">
                Found: {result.matches.length} matching report
                {result.matches.length === 1 ? "" : "s"}
              </StatusPill>
              {result.matches.map((m) => (
                <Card
                  key={`${m.source}-${m.mrv_report_id ?? m.report_id}`}
                  title={m.project.name ?? "Unnamed project"}
                  meta={`Source: ${m.source === "chain" ? "on-chain registry" : "AquaCreds database"}`}
                >
                  <dl className="grid grid-cols-2 gap-4 text-sm md:grid-cols-4">
                    <div>
                      <dt className="text-xs uppercase tracking-wide text-fg-muted">Period</dt>
                      <dd className="tabular">
                        {m.period_start}–{m.period_end}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-xs uppercase tracking-wide text-fg-muted">Status</dt>
                      <dd>
                        <StatusPill
                          status={
                            m.status === "approved"
                              ? "ok"
                              : m.status === "rejected"
                                ? "failed"
                                : "review"
                          }
                        >
                          {m.status}
                        </StatusPill>
                      </dd>
                    </div>
                    <div>
                      <dt className="text-xs uppercase tracking-wide text-fg-muted">
                        Anchored on-chain
                      </dt>
                      <dd>
                        {m.anchored ? "Yes" : "No"}
                        {m.report_id !== null && (
                          <span className="text-fg-muted">
                            {" "}
                            · report {formatNumber(m.report_id)}
                          </span>
                        )}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-xs uppercase tracking-wide text-fg-muted">Transaction</dt>
                      <dd>
                        {m.chain ? (
                          <TxLink
                            chainId={m.chain.chain_id}
                            txHash={m.chain.tx_hash}
                            explorerUrl={explorerUrlFor(m.chain.chain_id, m.chain.explorer_url)}
                          />
                        ) : (
                          "—"
                        )}
                      </dd>
                    </div>
                  </dl>
                  {m.project.id && (
                    <p className="mt-3 text-sm">
                      <a
                        className="underline underline-offset-2"
                        href={`/registry/projects/${m.project.id}`}
                      >
                        Open the project record
                      </a>
                    </p>
                  )}
                </Card>
              ))}
            </div>
          ) : (
            <div role="status">
              <StatusPill status="failed">No report with this hash</StatusPill>
              <p className="mt-2 text-sm text-fg-muted">
                Neither the registry database nor the chain knows{" "}
                <code className="break-all">{result.sha256_hex}</code>. Check for a typo; a file
                that was changed after anchoring will not match.
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function VerifyHashForm({ initial = "" }: { initial?: string }) {
  return (
    <PublicProviders>
      <Form initial={initial} />
    </PublicProviders>
  );
}
