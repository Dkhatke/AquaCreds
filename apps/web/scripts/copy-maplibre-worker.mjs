// MapLibre 6 locates its web worker next to its own module (import.meta.url). Bundled by Next.js,
// that URL points into a chunk folder where the worker does not exist, so we serve the worker
// as a static file and point MapLibre at it (`setWorkerUrl` in ProjectMap). Runs before dev/build.
import { copyFileSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const dist = dirname(require.resolve("maplibre-gl/package.json"));
const target = join(dirname(fileURLToPath(import.meta.url)), "../public/vendor");
mkdirSync(target, { recursive: true });
copyFileSync(join(dist, "dist/maplibre-gl-worker.mjs"), join(target, "maplibre-gl-worker.mjs"));
console.log("copied maplibre-gl-worker.mjs to public/vendor/");
