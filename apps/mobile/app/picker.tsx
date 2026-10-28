import { useRouter } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { t } from "../src/i18n";
import { type PeriodOut, useApp } from "../src/state/AppProvider";
import {
  type Cached,
  loadPlots,
  loadProjects,
  type ProjectWithPeriods,
} from "../src/state/fieldData";
import { colors, radius, space, touchTarget } from "../src/theme";
import { AppText, Banner, Button, Screen, StatusPill } from "../src/ui";

export default function Picker() {
  const { client, cache, online, select } = useApp();
  const router = useRouter();
  const [data, setData] = useState<Cached<ProjectWithPeriods[]> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      setData(await loadProjects(client, cache, online));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [client, cache, online]);

  useEffect(() => {
    void load();
  }, [load]);

  async function choose(entry: ProjectWithPeriods, period: PeriodOut) {
    // Cache the plots now, while there may still be signal.
    await loadPlots(client, cache, entry.project.id, online).catch(() => undefined);
    await select({ project: entry.project, period });
    router.dismissTo("/");
  }

  const projects = data?.value ?? [];
  return (
    <Screen>
      {!online ? <Banner tone="review" message={t("common.offline")} /> : null}
      {data?.cachedAt ? (
        <Banner
          tone="neutral"
          message={t("picker.cached", { when: new Date(data.cachedAt).toLocaleString() })}
        />
      ) : null}
      {error ? <Banner tone="failed" message={error} /> : null}
      {busy && !data ? <AppText>{t("common.loading")}</AppText> : null}
      {data && projects.length === 0 ? <AppText>{t("picker.noProjects")}</AppText> : null}

      {projects.map((entry) => (
        <View key={entry.project.id} style={styles.project}>
          <AppText variant="heading">{entry.project.name}</AppText>
          <AppText color={colors.textMuted}>{entry.project.state}</AppText>
          {entry.periods.length === 0 ? (
            <AppText color={colors.textMuted}>{t("picker.noOpenPeriods")}</AppText>
          ) : (
            entry.periods.map((p) => (
              <Pressable
                key={p.id}
                accessibilityRole="button"
                onPress={() => void choose(entry, p)}
                style={({ pressed }) => [styles.period, pressed && styles.pressed]}
              >
                <AppText variant="data">
                  {t("picker.period", { kind: p.kind, start: p.start_date, end: p.end_date })}
                </AppText>
                <StatusPill
                  tone={p.status === "changes_requested" ? "review" : "ok"}
                  label={t(
                    p.status === "changes_requested"
                      ? "picker.status.changes_requested"
                      : "picker.status.open",
                  )}
                />
              </Pressable>
            ))
          )}
        </View>
      ))}
      <Button
        label={t("common.retry")}
        variant="secondary"
        onPress={() => void load()}
        busy={busy}
      />
    </Screen>
  );
}

const styles = StyleSheet.create({
  project: {
    gap: space.sm,
    padding: space.md,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
  },
  period: {
    minHeight: touchTarget,
    gap: space.xs,
    padding: space.md,
    borderRadius: radius.sm,
    borderWidth: 2,
    borderColor: colors.text,
    backgroundColor: colors.surfaceMuted,
  },
  pressed: { opacity: 0.8 },
});
