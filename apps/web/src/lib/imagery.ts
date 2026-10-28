import provenance from "../../public/imagery/provenance.json";

/** Sentinel-2 imagery rendered by scripts/render_web_imagery.py (see public/imagery/provenance.json). */
export const imagery = {
  attribution: provenance.attribution,
  area: provenance.area.name,
  scenes: provenance.source.scenes,
  /** Acquisition dates, most recent first. */
  dates: [...new Set(provenance.source.scenes.map((s) => s.datetime.slice(0, 10)))]
    .sort()
    .reverse(),
  outputs: provenance.outputs,
};

function output(file: string) {
  const o = imagery.outputs.find((x) => x.file === file);
  if (!o) throw new Error(`imagery output ${file} missing from provenance.json`);
  return o;
}

export const heroImage = output("bhitarkanika-truecolour.webp");
export const falseColourImage = output("bhitarkanika-falsecolour.webp");

/** WGS84 overlay for MapLibre (corner coordinates TL, TR, BR, BL). */
export const mapOverlay = (() => {
  const o = output("bhitarkanika-truecolour-wgs84.webp");
  if (!("coordinates" in o) || !o.coordinates) throw new Error("overlay without coordinates");
  return {
    url: `/imagery/${o.file}`,
    coordinates: o.coordinates as [
      [number, number],
      [number, number],
      [number, number],
      [number, number],
    ],
  };
})();
