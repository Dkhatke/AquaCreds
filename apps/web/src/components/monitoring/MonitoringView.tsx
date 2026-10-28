"use client";

import type { Geometry } from "geojson";
import { useId, useState } from "react";
import { EmptyState, ErrorState, LoadingState, StatusPill } from "@/components/ui";
import { formatNumber, humanize } from "@/lib/format";
import { useClassifications, useProject, useTileJson } from "@/lib/queries";
import { PROJECT_STATUS } from "@/lib/status";
import { AlertsTimeline } from "./AlertsTimeline";
import { ClassificationPanel, latestClassification } from "./ClassificationPanel";
import { boundsOf, type ImageOverlay, ProjectMap } from "./ProjectMap";
import { StewardshipPanel } from "./StewardshipPanel";
import { TimeseriesPanel } from "./TimeseriesPanel";

function overlaps(geom: Geometry, overlay: ImageOverlay): boolean {
  const b = boundsOf(geom);
  if (!b) return false;
  const xs = overlay.coordinates.map((c) => c[0]);
  const ys = overlay.coordinates.map((c) => c[1]);
  return (
    b[0][0] < Math.max(...xs) &&
    b[1][0] > Math.min(...xs) &&
    b[0][1] < Math.max(...ys) &&
    b[1][1] > Math.min(...ys)
  );
}

export function MonitoringView({
  projectId,
  tilesEnabled,
  overlay,
}: {
  projectId: string;
  tilesEnabled: boolean;
  overlay: ImageOverlay;
}) {
  const project = useProject(projectId);
  const classifications = useClassifications(projectId);
  const latest = classifications.data ? latestClassification(classifications.data) : null;
  const tiles = useTileJson(projectId, latest?.id ?? null, tilesEnabled);
  const [showOverlay, setShowOverlay] = useState(true);
  const [showClasses, setShowClasses] = useState(true);
  const ids = { overlay: useId(), classes: useId() };

  if (project.isPending) return <LoadingState className="p-6" label="Loading project" />;
  if (project.isError) {
    return (
      <div className="p-6">
        <ErrorState
          title="This project could not be loaded"
          error={project.error}
          onRetry={() => void project.refetch()}
        />
      </div>
    );
  }
  const p = project.data;
  const geometry = p.boundary as unknown as Geometry;
  const overlayHere = overlaps(geometry, overlay) ? overlay : null;
  const st = PROJECT_STATUS[p.status];

  const classesNote = !tilesEnabled
    ? "Classification map tiles are not enabled on this deployment."
    : !latest
      ? "No classification to display yet."
      : tiles.isError
        ? (tiles.error as Error).message
        : tiles.isPending
          ? "Loading classification tiles…"
          : null;

  return (
    <div className="flex flex-col lg:block lg:h-[calc(100dvh-3.5rem)]">
      <div className="relative h-[55vh] lg:absolute lg:inset-0 lg:h-auto">
        <ProjectMap
          boundary={geometry}
          overlay={overlayHere}
          showOverlay={showOverlay}
          classes={tiles.data ?? null}
          showClasses={showClasses}
          label={`Map of ${p.name}`}
        />
        <section
          aria-labelledby="project-title"
          className="absolute left-3 top-3 z-10 w-[min(22rem,calc(100%-1.5rem))] rounded-[var(--radius-card)] border border-line bg-deep-ocean/90 p-4 shadow-lg"
        >
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill status={st.status}>{st.label}</StatusPill>
            <span className="text-xs text-fg-muted">
              {humanize(p.ecosystem)} · {p.state}
            </span>
          </div>
          <h1 id="project-title" className="mt-2 font-display text-xl font-semibold leading-tight">
            {p.name}
          </h1>
          <p className="tabular mt-1 text-sm text-fg-muted">
            Boundary area {formatNumber(p.area_ha, 2)} ha (geodesic)
          </p>
          <fieldset className="mt-3 space-y-1.5 border-t border-line pt-3 text-sm">
            <legend className="sr-only">Map layers</legend>
            <div className="flex items-center gap-2">
              <span aria-hidden="true" className="inline-block h-0.5 w-4 bg-lagoon-mint" />
              Project boundary
            </div>
            <div className="flex items-start gap-2">
              <input
                id={ids.overlay}
                type="checkbox"
                className="mt-1 accent-[#43C9A4]"
                checked={showOverlay && Boolean(overlayHere)}
                disabled={!overlayHere}
                onChange={(e) => setShowOverlay(e.target.checked)}
              />
              <label htmlFor={ids.overlay}>
                Sentinel-2 true colour (context)
                {!overlayHere && (
                  <span className="block text-xs text-fg-muted">
                    Only rendered for Bhitarkanika so far.
                  </span>
                )}
              </label>
            </div>
            <div className="flex items-start gap-2">
              <input
                id={ids.classes}
                type="checkbox"
                className="mt-1 accent-[#43C9A4]"
                checked={showClasses && Boolean(tiles.data)}
                disabled={!tiles.data}
                onChange={(e) => setShowClasses(e.target.checked)}
              />
              <label htmlFor={ids.classes}>
                Ecosystem classification{latest ? ` (${latest.model_version})` : ""}
                {classesNote && <span className="block text-xs text-fg-muted">{classesNote}</span>}
              </label>
            </div>
          </fieldset>
        </section>
      </div>

      <div className="relative z-10 space-y-3 p-3 lg:absolute lg:bottom-3 lg:right-3 lg:top-3 lg:w-[26rem] lg:overflow-y-auto lg:p-0">
        <ClassificationPanel query={classifications} />
        <TimeseriesPanel projectId={projectId} />
        <AlertsTimeline projectId={projectId} />
        <StewardshipPanel projectId={projectId} />
        {p.status === "draft" && (
          <EmptyState title="Draft project">
            Satellite ingestion starts after the project is submitted and registered, so most panels
            stay empty until then.
          </EmptyState>
        )}
      </div>
    </div>
  );
}
