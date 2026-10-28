/**
 * Evidence capture: in-app camera only (no gallery), with a server-issued capture nonce fetched
 * before the shot, a live GPS fix, heading, device model and app version. The photo file is moved
 * to app storage, hashed once, and queued in the outbox; nothing re-encodes it afterwards.
 */
import { ApiError } from "@aquacreds/api-client";
import * as Application from "expo-application";
import { CameraView, useCameraPermissions } from "expo-camera";
import Constants from "expo-constants";
import { randomUUID } from "expo-crypto";
import * as Device from "expo-device";
import * as Location from "expo-location";
import { useRouter } from "expo-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { StyleSheet, View } from "react-native";
import { issueNonce } from "../src/api/client";
import {
  additionalExifFor,
  canShoot,
  clockSkewWarning,
  type GpsFix,
  gpsQuality,
  type IssuedNonce,
  nonceRemainingMs,
  nonceUsable,
  normaliseHeading,
} from "../src/domain/capture";
import { geofence, parseBoundary } from "../src/geo/boundary";
import { t } from "../src/i18n";
import { detailMessage } from "../src/outbox/machine";
import type { SubmissionMeta } from "../src/outbox/types";
import { persistCapture, readFileBytes } from "../src/platform/files";
import { sha256Hex } from "../src/platform/sha256";
import { type PlotOut, useApp } from "../src/state/AppProvider";
import { loadPlots } from "../src/state/fieldData";
import { colors, radius, space } from "../src/theme";
import { AppText, Banner, Button, Choice, Screen } from "../src/ui";

type Kind = SubmissionMeta["kind"];

function deviceModel(): string {
  return [Device.manufacturer, Device.modelName].filter(Boolean).join(" ").slice(0, 120);
}

function appVersion(): string {
  return (Application.nativeApplicationVersion ?? Constants.expoConfig?.version ?? "").slice(0, 40);
}

function errorText(err: unknown): string {
  if (err instanceof ApiError) return detailMessage(err);
  return err instanceof Error ? err.message : String(err);
}

export default function Capture() {
  const { client, cache, outbox, online, selection, refreshOutbox, syncNow } = useApp();
  const router = useRouter();
  const camera = useRef<CameraView>(null);
  const [camPerm, requestCam] = useCameraPermissions();
  const [locPerm, requestLoc] = Location.useForegroundPermissions();
  const [fix, setFix] = useState<GpsFix | null>(null);
  const [heading, setHeading] = useState<number | null>(null);
  const [nonce, setNonce] = useState<IssuedNonce | null>(null);
  const [nonceError, setNonceError] = useState<string | null>(null);
  const [requesting, setRequesting] = useState(false);
  const [now, setNow] = useState(Date.now());
  const [kind, setKind] = useState<Kind>("general");
  const [plots, setPlots] = useState<PlotOut[]>([]);
  const [plotCode, setPlotCode] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const periodId = selection?.period.id;
  const projectId = selection?.project.id;
  const boundary = useMemo(() => parseBoundary(selection?.project.boundary), [selection]);

  // One-second clock for the nonce countdown and GPS staleness.
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    if (!projectId) return;
    loadPlots(client, cache, projectId, online)
      .then((r) => setPlots(r.value))
      .catch(() => undefined);
  }, [client, cache, projectId, online]);

  // Live GPS + heading while the screen is open.
  useEffect(() => {
    if (!locPerm?.granted) return;
    const subs: Location.LocationSubscription[] = [];
    let cancelled = false;
    void Location.watchPositionAsync(
      { accuracy: Location.Accuracy.BestForNavigation, timeInterval: 1000, distanceInterval: 0 },
      (loc) =>
        setFix({
          lat: loc.coords.latitude,
          lon: loc.coords.longitude,
          accuracyM: loc.coords.accuracy,
          altitudeM: loc.coords.altitude,
          timestamp: loc.timestamp,
          mocked: loc.mocked === true,
        }),
    ).then((s) => (cancelled ? s.remove() : subs.push(s)));
    void Location.watchHeadingAsync((h) =>
      setHeading(normaliseHeading(h.trueHeading >= 0 ? h.trueHeading : h.magHeading)),
    )
      .then((s) => (cancelled ? s.remove() : subs.push(s)))
      .catch(() => undefined); // no compass on some low-end phones: heading stays empty
    return () => {
      cancelled = true;
      for (const s of subs) s.remove();
    };
  }, [locPerm?.granted]);

  const requestNonce = useCallback(async () => {
    if (!periodId) return;
    setRequesting(true);
    setNonceError(null);
    try {
      setNonce(await issueNonce(client, periodId));
    } catch (err) {
      setNonceError(t("capture.nonceFailed", { message: errorText(err) }));
    } finally {
      setRequesting(false);
    }
  }, [client, periodId]);

  // Fetch a nonce when the camera opens, and again whenever the current one expires or is used.
  const usable = nonceUsable(nonce, now);
  useEffect(() => {
    if (!usable && online && !requesting && !nonceError) void requestNonce();
  }, [usable, online, requesting, nonceError, requestNonce]);

  if (!selection || !periodId || !projectId) return null;

  if (!camPerm?.granted || !locPerm?.granted) {
    return (
      <Screen title={t("capture.title")}>
        {!camPerm?.granted ? (
          <>
            <AppText>{t("capture.permissionCamera")}</AppText>
            <Button label={t("capture.grant")} onPress={() => void requestCam()} />
          </>
        ) : null}
        {!locPerm?.granted ? (
          <>
            <AppText>{t("capture.permissionLocation")}</AppText>
            <Button label={t("capture.grant")} onPress={() => void requestLoc()} />
          </>
        ) : null}
      </Screen>
    );
  }

  const quality = gpsQuality(fix, now);
  const fence = fix && boundary ? geofence(fix.lon, fix.lat, fix.accuracyM ?? 0, boundary) : null;
  const needsPlot = kind === "plot_centre" && !plotCode;
  const ready = usable && canShoot(quality) && !needsPlot && !saving && fix !== null;

  async function shoot() {
    if (!fix || !nonce || !camera.current || !periodId || !projectId) return;
    const shotFix = fix;
    const shotNonce = nonce;
    const shotHeading = heading;
    setSaving(true);
    setSaved(false);
    setSaveError(null);
    try {
      const capturedAt = new Date();
      const picture = await camera.current.takePictureAsync({
        quality: 0.8,
        exif: true,
        additionalExif: additionalExifFor(shotFix),
        imageType: "jpg",
      });
      const id = randomUUID();
      const uri = await persistCapture(picture.uri, `${id}.jpg`);
      const bytes = await readFileBytes(uri);
      const sha256 = await sha256Hex(bytes);
      await outbox.add({
        kind: "photo",
        projectId,
        periodId,
        payload: {
          fileUri: uri,
          contentType: "image/jpeg",
          sha256,
          sizeBytes: bytes.length,
          meta: {
            nonce: shotNonce.nonce,
            kind,
            plot_code: kind === "plot_centre" ? plotCode : null,
            lat: shotFix.lat,
            lon: shotFix.lon,
            gps_accuracy_m: shotFix.accuracyM ?? 0,
            heading_deg: shotHeading,
            captured_at: capturedAt.toISOString(),
            device_model: deviceModel(),
            app_version: appVersion(),
          },
        },
      });
      setNonce(null); // single use: the effect fetches a fresh one for the next shot
      setSaved(true);
      await refreshOutbox();
      void syncNow();
    } catch (err) {
      setSaveError(errorText(err));
    } finally {
      setSaving(false);
    }
  }

  const remainingMin = nonce ? Math.max(0, Math.floor(nonceRemainingMs(nonce, now) / 60_000)) : 0;

  return (
    <Screen>
      <View style={styles.preview}>
        <CameraView ref={camera} style={StyleSheet.absoluteFill} facing="back" mode="picture" />
      </View>

      {saved ? <Banner tone="ok" message={t("capture.saved")} /> : null}
      {saveError ? <Banner tone="failed" message={saveError} /> : null}

      <View style={styles.panel}>
        <AppText variant="label" color={colors.textMuted}>
          {t("capture.gps")}
        </AppText>
        {fix && quality !== "stale" ? (
          <>
            <AppText variant="data">
              {fix.lat.toFixed(6)}, {fix.lon.toFixed(6)}
            </AppText>
            <AppText variant="dataLarge">
              {t("capture.accuracy", { metres: Math.round(fix.accuracyM ?? 0) })}
            </AppText>
          </>
        ) : (
          <AppText>{t("capture.gpsWaiting")}</AppText>
        )}
      </View>
      {quality === "mocked" ? <Banner tone="failed" message={t("capture.gpsMocked")} /> : null}
      {quality === "poor" || quality === "unusable" ? (
        <Banner
          tone="review"
          message={t("capture.accuracyPoor", { metres: Math.round(fix?.accuracyM ?? 0) })}
        />
      ) : null}
      {!boundary ? <Banner tone="review" message={t("capture.noBoundary")} /> : null}
      {fence?.state === "inside" ? <Banner tone="ok" message={t("capture.inside")} /> : null}
      {fence?.state === "near" ? (
        <Banner
          tone="review"
          message={t("capture.outsideWithinAccuracy", { metres: Math.round(fence.outsideM) })}
        />
      ) : null}
      {fence?.state === "outside" ? (
        <Banner
          tone="failed"
          message={t("capture.outside", { metres: Math.round(fence.outsideM) })}
        />
      ) : null}

      <View style={styles.panel}>
        <AppText variant="label" color={colors.textMuted}>
          {t("capture.nonce")}
        </AppText>
        {nonce && usable ? (
          <AppText variant="data">
            {nonce.nonce.slice(0, 8)} · {t("capture.nonceExpires", { minutes: remainingMin })}
          </AppText>
        ) : (
          <AppText>{online ? t("capture.nonceExpired") : t("capture.nonceNeedsNetwork")}</AppText>
        )}
      </View>
      {nonce && clockSkewWarning(nonce.clockOffsetMs) ? (
        <Banner
          tone="failed"
          message={t("capture.clockSkew", { seconds: Math.round(nonce.clockOffsetMs / 1000) })}
        />
      ) : null}
      {nonceError ? (
        <>
          <Banner tone="failed" message={nonceError} />
          <Button
            label={t("common.retry")}
            variant="secondary"
            onPress={() => void requestNonce()}
          />
        </>
      ) : null}

      <Choice<Kind>
        label={t("capture.kind")}
        value={kind}
        onChange={setKind}
        options={[
          { value: "general", label: t("capture.kind.general") },
          { value: "photo_point", label: t("capture.kind.photo_point") },
          { value: "plot_centre", label: t("capture.kind.plot_centre") },
        ]}
      />
      {kind === "plot_centre" ? (
        plots.length ? (
          <Choice<string>
            label={t("capture.plot")}
            value={plotCode}
            onChange={setPlotCode}
            options={plots.map((p) => ({ value: p.code, label: `${p.code} · ${p.marker_id}` }))}
          />
        ) : (
          <Banner tone="review" message={t("measure.noPlots")} />
        )
      ) : null}
      {needsPlot && plots.length ? <AppText>{t("capture.plotRequired")}</AppText> : null}

      <Button
        label={saving ? t("capture.saving") : t("capture.shoot")}
        onPress={() => void shoot()}
        disabled={!ready}
        busy={saving}
      />
      <Button label={t("hub.outbox")} variant="secondary" onPress={() => router.push("/outbox")} />
    </Screen>
  );
}

const styles = StyleSheet.create({
  preview: {
    aspectRatio: 3 / 4,
    width: "100%",
    borderRadius: radius.md,
    overflow: "hidden",
    backgroundColor: colors.chrome,
  },
  panel: {
    gap: space.xs,
    padding: space.md,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
  },
});
