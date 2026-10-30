"use client";

import type { Feature, FeatureCollection, Geometry } from "geojson";
import type { GeoJSONSource, Map as MlMap, StyleSpecification } from "maplibre-gl";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import "maplibre-gl/dist/maplibre-gl.css";
import { BASEMAP_STYLE, boundsOf } from "@/components/monitoring/ProjectMap";
import { ErrorState } from "@/components/ui";

export interface MapProject {
  id: string;
  name: string;
  boundary: Geometry;
  /** Outline colour group: mint = registered/monitoring, amber = in review, red = rejected. */
  tone: "ok" | "review" | "failed" | "neutral";
}

const SRC = "aq-projects";
const TONE_COLOR: Record<MapProject["tone"], string> = {
  ok: "#43C9A4",
  review: "#E9AD54",
  failed: "#F08A80",
  neutral: "#A9C2BB",
};

export function combinedBounds(
  projects: Pick<MapProject, "boundary">[],
): [[number, number], [number, number]] | null {
  let w = Infinity;
  let s = Infinity;
  let e = -Infinity;
  let n = -Infinity;
  for (const p of projects) {
    const b = boundsOf(p.boundary);
    if (!b) continue;
    w = Math.min(w, b[0][0]);
    s = Math.min(s, b[0][1]);
    e = Math.max(e, b[1][0]);
    n = Math.max(n, b[1][1]);
  }
  return Number.isFinite(w)
    ? [
        [w, s],
        [e, n],
      ]
    : null;
}

function toCollection(projects: MapProject[]): FeatureCollection {
  return {
    type: "FeatureCollection",
    features: projects.map(
      (p): Feature => ({
        type: "Feature",
        geometry: p.boundary,
        properties: { id: p.id, name: p.name, color: TONE_COLOR[p.tone] },
      }),
    ),
  };
}

/** All project boundaries on one map. Clicking a boundary opens that project's monitoring. */
export function OverviewMap({ projects, label }: { projects: MapProject[]; label: string }) {
  const container = useRef<HTMLElement>(null);
  const mapRef = useRef<MlMap | null>(null);
  const latest = useRef(projects);
  latest.current = projects;
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The map is created once; later project changes are applied by the effect below.
  useEffect(() => {
    let cancelled = false;
    let map: MlMap | null = null;
    (async () => {
      try {
        const maplibre = await import("maplibre-gl");
        if (cancelled || !container.current) return;
        maplibre.setWorkerUrl(`${window.location.origin}/vendor/maplibre-gl-worker.mjs`);
        const bounds = combinedBounds(latest.current);
        map = new maplibre.Map({
          container: container.current,
          style: BASEMAP_STYLE as unknown as StyleSpecification,
          ...(bounds
            ? { bounds, fitBoundsOptions: { padding: 50, maxZoom: 12 } }
            : { center: [86.9, 20.7], zoom: 8 }),
          attributionControl: { compact: true },
        });
        map.addControl(new maplibre.NavigationControl({ visualizePitch: false }), "top-right");
        map.on("load", () => !cancelled && setReady(true));
        map.on("error", (e) => {
          if (!map?.isStyleLoaded() && e.error) setError(e.error.message);
        });
        mapRef.current = map;
      } catch (err) {
        setError(err instanceof Error ? err.message : "The map could not start");
      }
    })();
    return () => {
      cancelled = true;
      map?.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    const data = toCollection(projects);
    const src = map.getSource(SRC) as GeoJSONSource | undefined;
    if (src) src.setData(data);
    else {
      map.addSource(SRC, { type: "geojson", data });
      map.addLayer({
        id: "aq-projects-fill",
        type: "fill",
        source: SRC,
        paint: { "fill-color": ["get", "color"], "fill-opacity": 0.14 },
      });
      map.addLayer({
        id: "aq-projects-line",
        type: "line",
        source: SRC,
        paint: { "line-color": ["get", "color"], "line-width": 2.2 },
      });
      map.on("click", "aq-projects-fill", (e) => {
        const id = e.features?.[0]?.properties?.["id"];
        if (typeof id === "string") router.push(`/console/projects/${id}/monitoring`);
      });
      map.on("mouseenter", "aq-projects-fill", () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", "aq-projects-fill", () => {
        map.getCanvas().style.cursor = "";
      });
    }
    const b = combinedBounds(projects);
    if (b) map.fitBounds(b, { padding: 50, maxZoom: 12, duration: 0 });
  }, [ready, projects, router]);

  return (
    <div className="relative h-full min-h-72 w-full">
      <section ref={container} className="h-full w-full" aria-label={label} />
      {error && (
        <div className="absolute bottom-4 left-4 z-10 max-w-md">
          <ErrorState title="The map could not be displayed" error={error} />
        </div>
      )}
    </div>
  );
}
