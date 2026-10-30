"use client";

import type { Schema } from "@aquacreds/api-client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import {
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  LoadingState,
  Stat,
  StatusPill,
} from "@/components/ui";
import { browserClient } from "@/lib/api-browser";
import { problemMessages } from "@/lib/api-problems";
import { formatDate, formatNumber, humanize, shortHash } from "@/lib/format";
import { keys, useProject } from "@/lib/queries";
import { useMe, useSession } from "@/lib/registry-queries";
import { DOCUMENT_KIND, METHODOLOGY, TENURE } from "@/lib/registry-status";
import { PROJECT_STATUS } from "@/lib/status";
import { BoundaryThumb } from "./BoundaryThumb";
import { Field, inputClass, ProblemList } from "./fields";

const EDITABLE = new Set(["draft", "changes_requested"]);
const DOC_KINDS = Object.keys(DOCUMENT_KIND) as Schema<"DocumentKind">[];

export function ProjectDetail({ projectId }: { projectId: string }) {
  const qc = useQueryClient();
  const { data: p, isPending, isError, error, refetch } = useProject(projectId);
  const me = useMe();
  const session = useSession();
  const demo = session.data?.demo ?? false;
  const isAdmin = Boolean(
    p && me.data?.memberships.some((m) => m.org_id === p.org_id && m.role === "admin"),
  );
  const editable = p ? EDITABLE.has(p.status) : false;

  const [kind, setKind] = useState<Schema<"DocumentKind">>("forest_dept_agreement");
  const [file, setFile] = useState<File | null>(null);

  const submit = useMutation({
    mutationFn: () => browserClient().projects.submit(projectId),
    onSuccess: (updated) => {
      qc.setQueryData(keys.project(projectId), updated);
      void qc.invalidateQueries({ queryKey: keys.projects });
    },
  });
  const upload = useMutation({
    mutationFn: (f: File) => {
      const contentType = f.type as "application/pdf" | "image/jpeg" | "image/png" | "image/webp";
      return browserClient().projects.uploadDocument(projectId, f, {
        kind,
        filename: f.name,
        content_type: contentType,
      });
    },
    onSuccess: () => {
      setFile(null);
      void qc.invalidateQueries({ queryKey: keys.project(projectId) });
    },
  });

  if (isPending) return <LoadingState label="Loading project" className="p-8" />;
  if (isError)
    return (
      <div className="p-8">
        <ErrorState error={error} onRetry={() => void refetch()} />
      </div>
    );

  const st = PROJECT_STATUS[p.status];
  const canWrite = isAdmin && !demo;

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-4 md:p-8">
      <p className="text-sm">
        <Link href="/console/registry" className="text-fg-muted underline underline-offset-2">
          Project registry
        </Link>
      </p>
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl font-semibold">{p.name}</h1>
          <p className="mt-2 flex flex-wrap items-center gap-2 text-sm text-fg-muted">
            <StatusPill status={st.status}>{st.label}</StatusPill>
            {humanize(p.ecosystem)} · {p.state} · {METHODOLOGY[p.methodology]}
          </p>
        </div>
        <div className="flex gap-2">
          <Link
            href={`/console/projects/${p.id}/monitoring`}
            className="text-sm underline underline-offset-2"
          >
            Satellite monitoring
          </Link>
          <Link
            href={`/registry/projects/${p.id}`}
            className="text-sm underline underline-offset-2"
          >
            Public record
          </Link>
        </div>
      </header>

      <div className="grid gap-6 md:grid-cols-[1fr_2fr]">
        <Card title="Boundary" meta="Drawn from the stored boundary; area is geodesic (PostGIS)">
          <BoundaryThumb geometry={p.boundary} name={p.name} width={260} height={180} />
        </Card>
        <Card title="Project facts">
          <dl className="grid grid-cols-2 gap-4 md:grid-cols-3">
            <Stat label="Area" value={formatNumber(p.area_ha, 2)} unit="ha" />
            <Stat label="Start date" value={formatDate(p.start_date)} />
            <Stat label="Tenure" value={TENURE[p.tenure_type]} />
            <Stat
              label="Planned mangrove share"
              value={formatNumber(p.planned_mangrove_share_pct, 1)}
              unit="%"
            />
            <Stat
              label="Soil disturbance"
              value={formatNumber(p.soil_disturbance_pct, 1)}
              unit="%"
            />
            <Stat label="Submitted" value={p.submitted_at ? formatDate(p.submitted_at) : null} />
          </dl>
          {p.description && <p className="mt-4 text-sm text-fg-muted">{p.description}</p>}
        </Card>
      </div>

      <Card
        title="Tenure and consent documents"
        meta="Which documents are required depends on the tenure type and methodology; the registry checks them when you submit."
      >
        <DataTable
          caption="Attached documents"
          hideCaption
          rows={p.documents}
          rowKey={(d) => d.id}
          empty="No documents attached yet."
          columns={[
            { key: "kind", header: "Kind", cell: (d) => DOCUMENT_KIND[d.kind] },
            { key: "file", header: "File", cell: (d) => d.filename },
            {
              key: "hash",
              header: "SHA-256",
              cell: (d) => (
                <code className="text-xs" title={d.sha256}>
                  {shortHash(d.sha256, 8, 6)}
                </code>
              ),
            },
            {
              key: "size",
              header: "Size (KB)",
              numeric: true,
              cell: (d) => formatNumber(d.size_bytes / 1024, 0),
            },
            { key: "at", header: "Attached", cell: (d) => formatDate(d.created_at) },
          ]}
        />
        {canWrite && editable && (
          <form
            className="mt-5 grid gap-3 border-t border-line pt-4 md:grid-cols-[1fr_1fr_auto]"
            onSubmit={(e) => {
              e.preventDefault();
              if (file) upload.mutate(file);
            }}
          >
            <Field label="Document kind">
              {({ id }) => (
                <select
                  id={id}
                  className={inputClass}
                  value={kind}
                  onChange={(e) => setKind(e.target.value as Schema<"DocumentKind">)}
                >
                  {DOC_KINDS.map((k) => (
                    <option key={k} value={k}>
                      {DOCUMENT_KIND[k]}
                    </option>
                  ))}
                </select>
              )}
            </Field>
            <Field label="File (PDF, JPEG, PNG or WebP)">
              {({ id }) => (
                <input
                  id={id}
                  type="file"
                  accept="application/pdf,image/jpeg,image/png,image/webp"
                  className="block w-full text-sm file:mr-3 file:rounded-full file:border file:border-line file:bg-panel-raised file:px-3 file:py-2 file:text-sm file:text-fg"
                  onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                />
              )}
            </Field>
            <div className="flex items-end">
              <Button type="submit" disabled={!file || upload.isPending}>
                {upload.isPending ? "Uploading…" : "Attach"}
              </Button>
            </div>
          </form>
        )}
        {upload.isError && (
          <ProblemList
            title="The document was not attached"
            messages={problemMessages(upload.error)}
          />
        )}
      </Card>

      <Card title="Submit for review">
        {demo ? (
          <p className="text-sm text-fg-muted">
            The demo is read-only: projects cannot be edited or submitted.
          </p>
        ) : !isAdmin ? (
          <p className="text-sm text-fg-muted">Only an admin of this organisation can submit.</p>
        ) : !editable ? (
          <p className="text-sm text-fg-muted">
            This project is {st.label.toLowerCase()}; it can only be submitted from draft or after
            changes were requested.
          </p>
        ) : (
          <div className="space-y-3">
            <p className="text-sm text-fg-muted">
              The registry checks required documents, overlaps with other projects and the
              methodology rules. Anything blocking is listed below exactly as the API reports it.
            </p>
            <Button onClick={() => submit.mutate()} disabled={submit.isPending}>
              {submit.isPending ? "Submitting…" : "Submit project"}
            </Button>
            {submit.isError && (
              <ProblemList title="Submission blocked" messages={problemMessages(submit.error)} />
            )}
            {submit.isSuccess && (
              <p role="status" className="text-sm text-ok">
                Submitted. A reviewer will pick it up.
              </p>
            )}
          </div>
        )}
      </Card>
      {!isAdmin && !me.isPending && !demo && (
        <EmptyState title="View only">
          You are not an admin of this project's organisation.
        </EmptyState>
      )}
    </div>
  );
}
