"use client";

import type { Feature, Geometry } from "geojson";
import type { GeoJSONSource, Map as MlMap, StyleSpecification } from "maplibre-gl";
import { useEffect, useRef, useState } from "react";
import "maplibre-gl/dist/maplibre-gl.css";
import { ErrorState } from "@/components/ui";

/** Free basemap, no key: OpenFreeMap (attribution comes with its TileJSON). */
export const BASEMAP_STYLE = "https://tiles.openfreemap.org/styles/dark";

export interface ImageOverlay {
  url: string;
  coordinates: [[number, number], [number, number], [number, number], [number, number]];
  attribution: string;
}

export interface RasterTiles {
  tiles: string[];
  minzoom?: number | undefined;
  maxzoom?: number | undefined;
  bounds?: [number, number, number, number] | undefined;
  attribution?: string | undefined;
}

interface Props {
  boundary: Geometry;
  overlay: ImageOverlay | null;
  showOverlay: boolean;
  classes: RasterTiles | null;
  showClasses: boolean;
  label: string;
}

const BOUNDARY_SRC = "aq-boundary";
const OVERLAY_SRC = "aq-s2-context";
const CLASSES_SRC = "aq-classes";

export function boundsOf(geom: Geometry): [[number, number], [number, number]] | null {
  let w = Infinity;
  let s = Infinity;
  let e = -Infinity;
  let n = -Infinity;
  const visit = (c: unknown): void => {
    if (Array.isArray(c) && typeof c[0] === "number" && typeof c[1] === "number") {
      w = Math.min(w, c[0]);
      e = Math.max(e, c[0]);
      s = Math.min(s, c[1]);
      n = Math.max(n, c[1]);
    } else if (Array.isArray(c)) c.forEach(visit);
  };
  if (geom.type === "GeometryCollection") {
    for (const g of geom.geometries) visit("coordinates" in g ? g.coordinates : []);
  } else visit(geom.coordinates);
  return Number.isFinite(w)
    ? [
        [w, s],
        [e, n],
      ]
    : null;
}

function firstSymbolLayer(map: MlMap): string | undefined {
  return map.getStyle().layers?.find((l) => l.type === "symbol")?.id;
}

/** Full-bleed MapLibre map: basemap, optional Sentinel-2 context image, classification tiles and
 *  the project boundary on top. */
export function ProjectMap({ boundary, overlay, showOverlay, classes, showClasses, label }: Props) {
  const container = useRef<HTMLElement>(null);
  const mapRef = useRef<MlMap | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Create the map once; the boundary only sets the initial view (later changes are handled below).
  // biome-ignore lint/correctness/useExhaustiveDependencies: intentionally runs once.
  useEffect(() => {
    let cancelled = false;
    let map: MlMap | null = null;
    (async () => {
      try {
        const maplibre = await import("maplibre-gl");
        if (cancelled || !container.current) return;
        const bounds = boundsOf(boundary);
        map = new maplibre.Map({
          container: container.current,
          style: BASEMAP_STYLE as unknown as StyleSpecification,
          ...(bounds
            ? { bounds, fitBoundsOptions: { padding: 80, maxZoom: 15 } }
            : { center: [86.9, 20.7], zoom: 9 }),
          attributionControl: { compact: false },
          cooperativeGestures: false,
        });
        map.addControl(new maplibre.NavigationControl({ visualizePitch: false }), "bottom-left");
        map.addControl(new maplibre.ScaleControl({ unit: "metric" }), "bottom-left");
        map.on("load", () => !cancelled && setReady(true));
        map.on("error", (e) => {
          // Tile hiccups are routine; only report failures to load the style itself.
          if (!map?.isStyleLoaded() && e.error) setError(e.error.message);
        });
        mapRef.current = map;
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "The map could not start (WebGL unavailable?)",
        );
      }
    })();
    return () => {
      cancelled = true;
      map?.remove();
      mapRef.current = null;
    };
  }, []);

  // Boundary.
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    const data: Feature = { type: "Feature", geometry: boundary, properties: {} };
    const src = map.getSource(BOUNDARY_SRC) as GeoJSONSource | undefined;
    if (src) src.setData(data);
    else {
      map.addSource(BOUNDARY_SRC, { type: "geojson", data });
      map.addLayer({
        id: "aq-boundary-fill",
        type: "fill",
        source: BOUNDARY_SRC,
        paint: { "fill-color": "#43C9A4", "fill-opacity": 0.08 },
      });
      map.addLayer({
        id: "aq-boundary-line",
        type: "line",
        source: BOUNDARY_SRC,
        paint: { "line-color": "#43C9A4", "line-width": 2.5 },
      });
    }
    const b = boundsOf(boundary);
    if (b) map.fitBounds(b, { padding: 80, maxZoom: 15, duration: 0 });
  }, [ready, boundary]);

  // Sentinel-2 context image.
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    const has = Boolean(map.getSource(OVERLAY_SRC));
    if (overlay && showOverlay && !has) {
      map.addSource(OVERLAY_SRC, {
        type: "image",
        url: overlay.url,
        coordinates: overlay.coordinates,
      });
      map.addLayer(
        {
          id: "aq-s2-context",
          type: "raster",
          source: OVERLAY_SRC,
          paint: { "raster-opacity": 0.9 },
        },
        map.getLayer("aq-classes")
          ? "aq-classes"
          : map.getLayer("aq-boundary-fill")
            ? "aq-boundary-fill"
            : firstSymbolLayer(map),
      );
    } else if ((!overlay || !showOverlay) && has) {
      map.removeLayer("aq-s2-context");
      map.removeSource(OVERLAY_SRC);
    }
  }, [ready, overlay, showOverlay]);

  // Classification tiles (signed URLs from the API's TileJSON).
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    if (map.getSource(CLASSES_SRC)) {
      map.removeLayer("aq-classes");
      map.removeSource(CLASSES_SRC);
    }
    if (classes && showClasses) {
      map.addSource(CLASSES_SRC, {
        type: "raster",
        tiles: classes.tiles,
        tileSize: 256,
        ...(classes.minzoom !== undefined ? { minzoom: classes.minzoom } : {}),
        ...(classes.maxzoom !== undefined ? { maxzoom: classes.maxzoom } : {}),
        ...(classes.bounds ? { bounds: classes.bounds } : {}),
        ...(classes.attribution ? { attribution: classes.attribution } : {}),
      });
      map.addLayer(
        {
          id: "aq-classes",
          type: "raster",
          source: CLASSES_SRC,
          paint: { "raster-opacity": 0.75 },
        },
        map.getLayer("aq-boundary-fill") ? "aq-boundary-fill" : firstSymbolLayer(map),
      );
    }
  }, [ready, classes, showClasses]);

  return (
    <div className="absolute inset-0">
      <section ref={container} className="absolute inset-0" aria-label={label} />
      {overlay && showOverlay && (
        <p className="pointer-events-none absolute bottom-8 right-2 z-10 rounded bg-deep-ocean/80 px-2 py-0.5 text-[11px] text-fg-muted">
          {overlay.attribution}
        </p>
      )}
      {error && (
        <div className="absolute inset-x-4 top-20 z-10 max-w-md">
          <ErrorState title="The map could not be displayed" error={error} />
        </div>
      )}
    </div>
  );
}
