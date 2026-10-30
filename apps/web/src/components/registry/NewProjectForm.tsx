"use client";

import type { ProjectCreate, Schema } from "@aquacreds/api-client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button, Card, ErrorState, LoadingState } from "@/components/ui";
import { browserClient } from "@/lib/api-browser";
import { problemMessages } from "@/lib/api-problems";
import { humanize } from "@/lib/format";
import { keys } from "@/lib/queries";
import { useMe, useSession } from "@/lib/registry-queries";
import { ECOSYSTEMS, METHODOLOGY, TENURE } from "@/lib/registry-status";
import { Field, inputClass, ProblemList } from "./fields";

/** Accepts a bare geometry, a Feature, or a FeatureCollection with exactly one feature. */
export function parseBoundary(text: string): ProjectCreate["boundary"] {
  let json: unknown;
  try {
    json = JSON.parse(text);
  } catch {
    throw new Error("The boundary is not valid JSON");
  }
  let g = json as { type?: string; geometry?: unknown; features?: unknown[] } | null;
  if (g?.type === "FeatureCollection") {
    if (!Array.isArray(g.features) || g.features.length !== 1)
      throw new Error("The FeatureCollection must contain exactly one feature");
    g = g.features[0] as typeof g;
  }
  if (g?.type === "Feature") g = g.geometry as typeof g;
  if (!g || (g.type !== "Polygon" && g.type !== "MultiPolygon"))
    throw new Error("The boundary must be a GeoJSON Polygon or MultiPolygon in WGS84 (lon, lat)");
  return g as ProjectCreate["boundary"];
}

export function NewProjectForm() {
  const router = useRouter();
  const qc = useQueryClient();
  const me = useMe();
  const session = useSession();
  const [local, setLocal] = useState<string[]>([]);
  const [boundaryText, setBoundaryText] = useState("");

  const create = useMutation({
    mutationFn: (body: ProjectCreate) => browserClient().projects.create(body),
    onSuccess: (p) => {
      void qc.invalidateQueries({ queryKey: keys.projects });
      router.push(`/console/registry/${p.id}`);
    },
  });

  if (me.isPending || session.isPending)
    return <LoadingState label="Checking your access" className="p-8" />;
  if (me.isError)
    return (
      <div className="p-8">
        <ErrorState error={me.error} onRetry={() => void me.refetch()} />
      </div>
    );

  const adminOrgs = me.data.memberships.filter((m) => m.role === "admin");
  const demo = session.data?.demo ?? false;
  const blocked = demo
    ? "The demo is read-only: projects cannot be registered here."
    : adminOrgs.length === 0
      ? "Only organisation admins can register projects. You are not an admin of any organisation."
      : null;

  const onSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const str = (k: string) => String(f.get(k) ?? "");
    try {
      const boundary = parseBoundary(boundaryText);
      setLocal([]);
      create.mutate({
        name: str("name").trim(),
        org_id: str("org_id"),
        state: str("state").trim(),
        description: str("description").trim(),
        ecosystem: str("ecosystem") as Schema<"Ecosystem">,
        methodology: str("methodology") as Schema<"Methodology">,
        tenure_type: str("tenure_type") as Schema<"TenureType">,
        start_date: str("start_date"),
        planned_mangrove_share_pct: Number(str("planned_mangrove_share_pct")),
        soil_disturbance_pct: Number(str("soil_disturbance_pct")),
        boundary,
      });
    } catch (err) {
      setLocal([err instanceof Error ? err.message : "Invalid boundary"]);
    }
  };

  return (
    <div className="mx-auto max-w-3xl p-4 md:p-8">
      <h1 className="font-display text-3xl font-semibold">Register a project</h1>
      <p className="mt-2 text-fg-muted">
        Creates a draft. Attach tenure documents on the next page, then submit for review. Nothing
        is registered on-chain until the project is approved.
      </p>
      {blocked ? (
        <p role="note" className="mt-6 rounded-lg border border-line px-4 py-3 text-sm">
          {blocked}
        </p>
      ) : (
        <Card className="mt-6">
          <form onSubmit={onSubmit} className="grid gap-4 md:grid-cols-2">
            <Field label="Project name" className="md:col-span-2">
              {({ id }) => (
                <input
                  id={id}
                  name="name"
                  required
                  minLength={3}
                  maxLength={200}
                  className={inputClass}
                />
              )}
            </Field>
            <Field label="Organisation">
              {({ id }) => (
                <select id={id} name="org_id" className={inputClass} required>
                  {adminOrgs.map((m) => (
                    <option key={m.org_id} value={m.org_id}>
                      Organisation {m.org_id.slice(0, 8)}
                    </option>
                  ))}
                </select>
              )}
            </Field>
            <Field label="State">
              {({ id }) => (
                <input
                  id={id}
                  name="state"
                  required
                  minLength={2}
                  maxLength={80}
                  placeholder="Odisha"
                  className={inputClass}
                />
              )}
            </Field>
            <Field label="Ecosystem">
              {({ id }) => (
                <select id={id} name="ecosystem" className={inputClass} defaultValue="mangrove">
                  {ECOSYSTEMS.map((x) => (
                    <option key={x} value={x}>
                      {humanize(x)}
                    </option>
                  ))}
                </select>
              )}
            </Field>
            <Field label="Methodology">
              {({ id }) => (
                <select
                  id={id}
                  name="methodology"
                  className={inputClass}
                  defaultValue="ccts_bm_fr05_001"
                >
                  {Object.entries(METHODOLOGY).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v}
                    </option>
                  ))}
                </select>
              )}
            </Field>
            <Field label="Land tenure">
              {({ id }) => (
                <select id={id} name="tenure_type" className={inputClass} required>
                  {Object.entries(TENURE).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v}
                    </option>
                  ))}
                </select>
              )}
            </Field>
            <Field label="Start date" hint="CCTS projects must start on or after 2025-01-01.">
              {({ id, describedBy }) => (
                <input
                  id={id}
                  name="start_date"
                  type="date"
                  required
                  aria-describedby={describedBy}
                  className={inputClass}
                />
              )}
            </Field>
            <Field label="Planned mangrove share (%)" hint="CCTS BM FR05.001 needs more than 90 %.">
              {({ id, describedBy }) => (
                <input
                  id={id}
                  name="planned_mangrove_share_pct"
                  type="number"
                  min={0}
                  max={100}
                  step="any"
                  required
                  aria-describedby={describedBy}
                  className={inputClass}
                />
              )}
            </Field>
            <Field label="Soil disturbance (%)" hint="CCTS BM FR05.001 allows at most 10 %.">
              {({ id, describedBy }) => (
                <input
                  id={id}
                  name="soil_disturbance_pct"
                  type="number"
                  min={0}
                  max={100}
                  step="any"
                  required
                  aria-describedby={describedBy}
                  className={inputClass}
                />
              )}
            </Field>
            <Field label="Description" className="md:col-span-2">
              {({ id }) => (
                <textarea
                  id={id}
                  name="description"
                  maxLength={5000}
                  rows={3}
                  className={`${inputClass} h-auto py-2`}
                />
              )}
            </Field>
            <Field
              label="Boundary (GeoJSON Polygon or MultiPolygon, WGS84)"
              className="md:col-span-2"
              hint="Paste GeoJSON or load a .geojson file. The registry rejects invalid, too-large and overlapping boundaries and says why."
            >
              {({ id, describedBy }) => (
                <>
                  <input
                    type="file"
                    accept=".geojson,.json,application/geo+json,application/json"
                    aria-label="Load a GeoJSON file"
                    className="mb-2 block text-sm"
                    onChange={async (e) => {
                      const file = e.target.files?.[0];
                      if (file) setBoundaryText(await file.text());
                    }}
                  />
                  <textarea
                    id={id}
                    aria-describedby={describedBy}
                    required
                    rows={7}
                    spellCheck={false}
                    value={boundaryText}
                    onChange={(e) => setBoundaryText(e.target.value)}
                    className={`${inputClass} h-auto py-2 font-mono text-xs`}
                  />
                </>
              )}
            </Field>
            <div className="space-y-3 md:col-span-2">
              <ProblemList title="Check the form" messages={local} />
              {create.isError && (
                <ProblemList
                  title="The registry rejected this project"
                  messages={problemMessages(create.error)}
                />
              )}
              <Button type="submit" disabled={create.isPending}>
                {create.isPending ? "Creating…" : "Create draft project"}
              </Button>
            </div>
          </form>
        </Card>
      )}
    </div>
  );
}
