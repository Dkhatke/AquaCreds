# AquaCreds Field (`apps/mobile`)

Expo app for field agents. It captures geotagged photo evidence bound to a capture nonce, records
sample-plot measurements, and keeps both in an offline queue (the outbox) that syncs through the
API when the phone has signal. Android is the target: EAS free tier, low-end phones, outdoor use.

## Run it

```sh
npx -y pnpm@12.14.0 install                         # from the repo root
cd apps/mobile
EXPO_PUBLIC_API_URL=http://<your-LAN-IP>:8000 pnpm start   # Expo dev server; open in a dev build
pnpm typecheck && pnpm lint && pnpm test
EXPO_PUBLIC_API_URL=https://api.example.invalid pnpm export   # Metro bundle (what CI runs)
```

`EXPO_PUBLIC_API_URL` is inlined at build time and has **no default**. A build without it shows an
error; it never falls back to a guessed host. `expo-camera`, `expo-location`, `expo-sqlite` and
`expo-secure-store` are native modules, so you need a development build (`eas build --profile
development`) or a release APK. Expo Go may lag behind the SDK.

## Screens (expo-router, `app/`)

| Route | What it does |
|---|---|
| `sign-in` | Email + password. If the API returns an `MfaChallenge`, it asks for a TOTP or recovery code (`auth.verifyMfa`) and warns when few recovery codes remain. Tokens are stored in expo-secure-store only. |
| `picker` | Projects and their **editable** periods (`open` or `changes_requested`, the statuses in which the API accepts evidence). Projects (with boundaries), periods and plots are cached in SQLite for offline use. |
| `index` | The hub for the chosen period: capture, measure, outbox counts. |
| `capture` | In-app camera only (no gallery). See [Capture](#capture). |
| `measure` | Plot measurement form: plot, date, and per tree the species, DBH, height and alive/dead. |
| `outbox` | Every queued item with its status, attempts, next retry and last error. Once the server has checked a photo, it shows the trust score and each check (pass/warn/fail/skipped with the server's reason). Retry, or remove after a confirmation. |

## Capture

1. When the screen opens, the app requests a nonce (`POST /periods/{id}/nonces`). It shows the
   first 8 characters and a countdown, and requests a new nonce when the current one is within
   30 s of expiry or has been used. The `Date` header of the nonce response gives the phone's clock
   offset. If the offset is above 30 s the app warns, because the worker hard-fails a `captured_at`
   that falls more than 60 s outside the nonce window, and `captured_at` comes from the phone clock.
2. GPS runs continuously (`BestForNavigation`, 1 s). The screen shows the coordinates and
   accuracy with tabular numerals. **Shooting is blocked** when there is no fix, the fix is older
   than 15 s, the accuracy is above 500 m (the API limit), or Android/iOS flags the position as
   **mocked**. Accuracy worse than 30 m shows a warning but shooting is allowed.
3. The local geofence (`src/geo/boundary.ts`: ray casting, holes, multipolygons, edges count as
   inside) uses the same thresholds as the worker's `check_geofence`. Inside the boundary is
   mint. Outside by no more than accuracy + 10 m is amber. Further outside is red, and the server
   will hard-fail that photo. The app only warns; the server decides.
4. On the shot, the app saves `captured_at` (UTC ISO), the fix, heading (true heading, falling back
   to magnetic; empty without a compass), `device_model` (manufacturer + model) and `app_version`.
   It then **moves** the JPEG from the camera cache into `documents/evidence/` (Android can purge
   the cache), hashes it, and queues it.

## Integrity: hash the exact bytes, never re-encode

- `takePictureAsync({ quality: 0.8, exif: true, additionalExif })` performs expo-camera's single
  JPEG encode and EXIF write. Both happen **before** the file URI is returned.
- The file is moved, not copied or re-encoded. Its bytes are read once (`File.bytes()`) and hashed
  with expo-crypto `digest(SHA256)`. That hash is stored with the outbox item.
- At upload, the bytes are read again and re-hashed. On a mismatch the item fails ("file changed
  after capture") and nothing is sent. The **same verified `Uint8Array`** goes to
  `field.uploadPhotoSubmission`. The client hashes it again (injected expo-crypto `sha256`) and
  PUTs it to the presigned URL through `expo/fetch`, which sends typed-array bodies as-is. React
  Native's XHR fetch is not trusted with binary bodies. The server re-hashes the stored object and
  rejects a mismatch.
- `test/sync.test.ts` asserts that the PUT body is byte-for-byte the captured file, that the PUT
  carries no `Authorization` header, and that the submission carries the same SHA-256.

## EXIF: what expo-camera writes vs what the server checks

This comes from reading the expo-camera 58.0.12 native sources (Android
`ResolveTakenPicture.kt`, `CameraViewHelper.kt`, `ExifTags.kt`; iOS `CapturedPhotoProcessor.swift`)
and `services/worker/src/aqua_worker/evidence.py`. It has **not yet been checked on a device**.
Verify on the target phones with `exiftool` before relying on it.

| Server reads (`read_exif` / `check_exif`) | Android (expo-camera, `exif: true`, `skipProcessing: false`) | Consequence |
|---|---|---|
| `GPSLatitude/Longitude` (+Ref) → `exif_gps`: warn if > max(50 m, 3 × accuracy) from the app GPS | Written by expo-camera from **our** `additionalExif` (`ExifInterface.setLatLong`). The camera adds no GPS of its own. | Always matches the app GPS. It is a **consistency check, not independent evidence**: it catches metadata edited after capture, not a spoofed fix. That is why mocked fixes are blocked on the phone. |
| `DateTimeOriginal` + `OffsetTimeOriginal` → `exif_time` (±10 min if a timezone is present) | `DateTimeOriginal`/`DateTime`/`DateTimeDigitized` are copied from the camera HAL's JPEG when the HAL writes them (device-dependent). **`OffsetTimeOriginal` is not in expo-camera's tag list, so it is dropped on re-encode.** | The server falls back to its date-only comparison (±1 day): `pass`, but weak. If the HAL writes no time, the result is `warn`. |
| `Make`, `Model`, `Software` (stored only) | `Make`/`Model` are copied (in the tag list). | Reported to verifiers. The server does not compare them with `device_model`. |
| (not read) `GPSDateStamp`/`GPSTimeStamp` (UTC) | Could be written through `additionalExif` (they are in the tag list). | Unused for now. See API gaps. |

With `skipProcessing: true` the raw HAL JPEG would be kept (possibly with `OffsetTimeOriginal`),
but `additionalExif` would **not** be applied (no GPS) and orientation would be unreliable. So the
app keeps processing on. iOS keeps the capture's EXIF dictionary (including `OffsetTimeOriginal`)
and merges our GPS; iOS is out of scope for builds today.

## Outbox (offline-first)

`src/outbox/`: the pure state machine (`machine.ts`), SQLite persistence (`store.ts`, written
against the expo-sqlite async API) and the sync engine (`sync.ts`, with injected dependencies).

```
queued ─start─▶ sending ─accepted─▶ awaiting_checks ─checked─▶ done      (photo)
                  │               └─────────(measurement)──────▶ done
                  ├─ retryable (offline, 5xx, 408/429, storage 403) ─▶ retry_wait ─(due)─▶ sending
                  ├─ permanent (other 4xx, file missing/changed) ─▶ failed ─retry─▶ queued
                  └─ auth (refresh failed) / app killed mid-send ─▶ queued
```

- Backoff starts at 15 s and doubles each attempt with ±20% jitter, capped at 30 min. Network
  errors never become permanent, because crews can be offline for days.
- A sync pass runs on start, after sign-in, when the network returns, when the app comes to the
  foreground, every 60 s while the app is open, and from "Sync now". It handles one item at a
  time. Concurrent triggers share one pass.
- **Lost responses.** If the submission was created but the reply was lost, the retry gets
  `409 Capture nonce already used`. The engine then lists the period's submissions and adopts the
  one with the same SHA-256. Only if none matches does the item fail.
- After a photo reaches `done`, its local file is deleted. Queued work survives sign-out and syncs
  after the next sign-in. Nonces are bound to the user, so another account cannot sync it.

## Plot measurements

`src/domain/validation.ts` mirrors `TreeRecord`/`MeasurementCreate`/`record_measurement`:
0 < DBH ≤ 300 cm, 0 < height ≤ 60 m (optional), 1–2000 trees, notes ≤ 2000 characters, and a
date that is a real calendar date inside the period and not in the future. One rule is stricter
than the API: the **species must be one the carbon engine has a wood density for**
(`src/domain/species.ts`, generated from `data/reference/wood_density_mangroves.csv`). Genus rows
appear as `"<Genus> sp."`, which `aqua_mrv` resolves to the genus mean.
`test/species.test.ts` fails if the list drifts from the CSV.

## Design

The tokens in `src/theme` come from `docs/design/DESIGN_DIRECTION.md`. The app uses only **light
surfaces** (it is read in direct sun) with deep-ocean text. Mint, amber and red are status fills
with deep-ocean or white text. The `*Ink` variants are the text-on-light forms, because mint and
amber are too light to read as text. Failed red is `#B42318` on light surfaces and `#FF8A80` on
dark ones (no single red reaches 4.5:1 on both off-white and deep-ocean). `test/theme.test.ts`
asserts WCAG AA contrast for every text/surface pair. Touch targets are at least 56 dp. Data
values use tabular numerals. Space Grotesk and Inter are bundled through `@expo-google-fonts`, with
no runtime downloads. There are no icons, and colour is never the only signal. Strings live in
`src/i18n/en.ts` (typed keys, `{param}` placeholders), ready for Hindi, Odia and other locales.

## Tests

`pnpm test` (vitest, Node) covers:

- the state machine: every transition, invalid transitions, backoff and error classification;
- the SQLite store, run on `node:sqlite` with the app's own SQL;
- the full upload flow against the real `@aquacreds/api-client` with a mocked fetch: exact bytes,
  offline retry, a changed file, a 4xx, an expired presigned URL, lost-response recovery, auth
  pause, single-flight sync, and measurements;
- point-in-polygon, distance and geofence; form validation; capture rules (nonce expiry on the
  server clock, clock skew, GPS grading, mocked fixes);
- the species list against the CSV; theme contrast;
- the expo-crypto, expo-secure-store, expo-file-system and `expo/fetch` adapters with mocked
  modules.

CI (`.github/workflows/frontend.yml`) runs the same suite through turbo and also bundles the app
with `expo export` to catch Metro resolution errors. No emulator is used.

## Pinned versions

Expo SDK **58**: `expo` 58.0.19, `expo-router` 58.0.26, `expo-camera` 58.0.12, `expo-file-system`
58.0.11, `expo-location` 58.0.19, `expo-sqlite` 58.0.14, `expo-crypto` 58.0.7, `expo-secure-store`
58.0.2, `react-native` 0.88.0, `react` 19.3.0.

`expo-doctor` reports patch releases (expo 58.0.20, router 58.0.27, camera 58.0.13, file-system
58.0.12) that came out on 2026-10-27. pnpm's `minimumReleaseAge` supply-chain gate rejects them
until they are a day old, so bump them in a later PR. TypeScript 7 is the repo standard (D25), so
`typescript` is in `expo.install.exclude` (Expo expects ~6.0).

## Known limitations / next steps

- **A nonce needs a connection at capture time.** The app explains this when offline. Uploads can
  wait, but the shot cannot. The API now offers pre-issued offline nonces (see "Offline capture"
  below); the app does not use them yet.
- There is no background sync (expo-background-task): sync runs only while the app is open.
- Not yet built: the boundary walk / KML import, the map preview (MapLibre RN), GPS averaging at
  plot centres, the sample-size calculator, tide awareness, and Hindi strings.
- `done` items are not pruned from the outbox list yet.

## Offline capture: pre-issued nonces (API ready, app not yet)

`POST /api/v1/periods/{period_id}/nonces/batch` with `{"count": N}` (N <= 20; field agents and org
admins of the project) returns N single-use nonces, all bound to the caller and the period and
valid for `CAPTURE_NONCE_BATCH_TTL_H` (default **72 h**). An agent may hold at most
`CAPTURE_NONCE_BATCH_MAX_OUTSTANDING` (40) unused, unexpired batch nonces per period (409 above
that). Fetch a batch while there is signal (e.g. at the boat jetty), store it in SecureStore, and
use one per shot exactly like a live nonce.

**Trade-off.** A live nonce (15 min) proves the photo was taken in the minutes after the server
issued it, so a photo taken earlier (gallery, another trip) is caught. A batch nonce proves only
that the photo was taken *after the batch was issued*, up to 72 h before; within that window an
agent could reuse a photo taken earlier on the same trip. It still prevents photos from before the
batch, photos used twice (single use, plus the SHA-256 and pHash duplicate checks), and use by
another agent or period. So the worker scores it as a separate, **lower-weight** result:
`capture_nonce` = `warn` with `weight: 0.5` and `details.nonce_mode: "batch"` (trust score 90
instead of 100 when everything else passes), and the verifier sees why. A capture before issue or
after expiry, or a reused/foreign nonce, is still a hard failure in either mode. Keep batches small
and TTLs short: a lost phone exposes at most the unused nonces of its own agent and period, until
they expire.

Live nonces now also return `issued_at`, `server_time` and `mode`, so the clock offset no longer
needs the HTTP `Date` header.

## API gaps found while building this

Status after the API-gaps PR: 1 and 2 resolved in the API (batch nonces, `issued_at` /
`server_time`); 5, 6 and 7 resolved (`Idempotency-Key` on submission and measurement creates,
replayed for 24 h, the submission replay ignoring `object_key` so a re-upload is fine;
`GET /periods/{id}/plots/{plot_id}/measurements` and `GET /periods/{id}/measurements` read-backs;
`listPlots` / `recordMeasurement` / `listMeasurements` / `issueNonceBatch` helpers;
`GET /periods/{id}/submissions` is paginated, `limit` <= 500 default 100, `X-Total-Count`, with a
`sha256` filter, which the outbox now uses for lost-response recovery). 3 and 4 are still open.
The app itself does not send idempotency keys or use batch nonces yet.

1. **No offline nonces.** `POST /periods/{id}/nonces` requires signal at the moment of capture,
   which is exactly what coastal sites lack. Options: pre-issue a short batch of longer-TTL nonces
   bound to device and period, or accept a signed GNSS time.
2. **`NonceOut` has no `issued_at` or server time.** The clock offset is estimated from the HTTP
   `Date` header (1 s resolution, and proxies may rewrite it).
3. **No `location_mocked` (or provider) field** in `SubmissionCreate`. The app blocks mocked fixes
   locally, but the server cannot record or check this.
4. **EXIF time.** Android expo-camera drops `OffsetTimeOriginal`, so `exif_time` only compares
   dates. The worker could also read `GPSDateStamp`/`GPSTimeStamp` (UTC), which the app could
   write, though like EXIF GPS that only checks consistency.
5. **Measurements are not idempotent and cannot be read back.** There is no
   `GET /periods/{id}/plots/{plot_id}/measurements` and no idempotency key. A lost response turns
   the retry into `409 already measured`, which the app must show as "needs attention" because it
   cannot confirm the record is its own.
6. **`@aquacreds/api-client` has no plot helpers** (`listPlots`, `recordMeasurement`). The app
   calls the raw typed `api` instead.
7. **Uploads and submissions have no idempotency key.** Photo recovery works by matching the
   SHA-256 in `GET /periods/{id}/submissions`, which returns every submission in the period
   (unpaginated).
