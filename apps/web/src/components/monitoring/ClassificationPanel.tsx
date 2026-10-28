"use client";

import type { Schema } from "@aquacreds/api-client";
import {
  Card,
  EmptyState,
  ErrorState,
  LoadingState,
  ProvenanceNote,
  Stat,
  StatusPill,
} from "@/components/ui";
import { formatDate, formatNumber, formatPercent, humanize, shortHash } from "@/lib/format";
import { modelCard } from "@/lib/model-cards";

type Classification = Schema<"ClassificationOut">;

/** Bar colours for the class-area breakdown (no purple, distinguishable on the dark panel). */
export const CLASS_COLOURS: Record<string, string> = {
  mangrove: "#43C9A4",
  water: "#4F9BD9",
  tidal_flat: "#C9B27C",
  cropland_aquaculture: "#E9AD54",
  terrestrial_other: "#8DB36B",
  built_bare: "#B8A79C",
};

export function latestClassification(rows: Classification[]): Classification | null {
  return (
    [...rows].sort(
      (a, b) =>
        b.season_start_year - a.season_start_year || b.created_at.localeCompare(a.created_at),
    )[0] ?? null
  );
}

export function ClassificationSummary({ c }: { c: Classification }) {
  const card = modelCard(c.model_version);
  const areas = Object.entries(c.class_areas_ha).sort((a, b) => b[1] - a[1]);
  const total = areas.reduce((s, [, v]) => s + v, 0);
  const season = `Nov ${c.season_start_year} – Mar ${c.season_start_year + 1}`;
  return (
    <div className="space-y-4">
      <dl className="grid grid-cols-2 gap-4">
        <Stat
          label="Mangrove"
          value={formatNumber(c.mangrove_ha, 2)}
          unit="ha"
          hint={`Dry season ${season}`}
        />
        <Stat
          label="Mean confidence"
          value={
            c.mangrove_mean_confidence === null ? null : formatPercent(c.mangrove_mean_confidence)
          }
          hint={`${formatPercent(c.low_confidence_share)} of pixels below ${formatPercent(c.low_confidence_threshold, 0)}`}
        />
      </dl>

      <div>
        <h3 className="text-xs font-medium uppercase tracking-wide text-fg-muted">Class areas</h3>
        <ul className="mt-2 space-y-1.5">
          {areas.map(([name, ha]) => (
            <li key={name} className="grid grid-cols-[8.5rem_1fr_auto] items-center gap-2 text-sm">
              <span className="truncate">{humanize(name)}</span>
              <span className="h-2 overflow-hidden rounded bg-panel-raised" aria-hidden="true">
                <span
                  className="block h-full rounded"
                  style={{
                    width: `${total ? (ha / total) * 100 : 0}%`,
                    background: CLASS_COLOURS[name] ?? "#A9C2BB",
                  }}
                />
              </span>
              <span className="tabular text-right">{formatNumber(ha, 2)} ha</span>
            </li>
          ))}
        </ul>
      </div>

      <div className="rounded-lg border border-line p-3">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-medium">Model {c.model_version}</h3>
          {card ? (
            <StatusPill status="ok">Held-out metrics on file</StatusPill>
          ) : (
            <StatusPill status="review">No model card for this version</StatusPill>
          )}
        </div>
        {card ? (
          <>
            <dl className="mt-3 grid grid-cols-3 gap-3">
              <Stat label="Mangrove F1" value={formatPercent(card.heldout.mangrove_vs_rest_f1)} />
              <Stat label="Macro-F1" value={formatPercent(card.heldout.macro_f1)} />
              <Stat label="Accuracy" value={formatPercent(card.heldout.accuracy)} />
            </dl>
            <p className="mt-2 text-xs text-fg-muted">
              Held out: {card.heldout.region}; n = {formatNumber(card.heldout.n)} points. Weakest
              class: tidal flat (F1 {formatPercent(card.heldout.per_class.tidal_flat.f1)}).{" "}
              {card.note}
            </p>
          </>
        ) : (
          <p className="mt-2 text-xs text-fg-muted">
            This web build has no held-out metrics for this model version, so none are shown.
          </p>
        )}
        <p className="mt-2 font-mono text-[11px] text-fg-muted" title={c.model_sha256}>
          model sha256 {shortHash(c.model_sha256, 10, 6)}
        </p>
      </div>

      <ProvenanceNote
        source={`${c.provider}, ${c.scenes.length} scenes, EPSG:${c.epsg}`}
        asOf={formatDate(c.created_at)}
        label="Context only: credits come from field plots"
      />
    </div>
  );
}

export function ClassificationPanel({
  query,
}: {
  query: {
    isPending: boolean;
    isError: boolean;
    error: unknown;
    data: Classification[] | undefined;
    refetch: () => unknown;
  };
}) {
  return (
    <Card title="Ecosystem classification" meta="Per 10 m pixel inside the boundary">
      {query.isPending ? (
        <LoadingState label="Loading classification" />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => void query.refetch()} />
      ) : (
        (() => {
          const latest = latestClassification(query.data ?? []);
          return latest ? (
            <ClassificationSummary c={latest} />
          ) : (
            <EmptyState title="Not classified yet">
              No ecosystem classification has been run for this project. Staff can start one for a
              dry season; results appear here with the model version and its validation.
            </EmptyState>
          );
        })()
      )}
    </Card>
  );
}
