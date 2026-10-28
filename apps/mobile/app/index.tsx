import { Redirect, useRouter } from "expo-router";
import { StyleSheet, View } from "react-native";
import { t } from "../src/i18n";
import { useApp } from "../src/state/AppProvider";
import { colors, radius, space } from "../src/theme";
import { AppText, Banner, Button, Screen, StatusPill } from "../src/ui";

export default function Hub() {
  const { selection, counts, online, signOut } = useApp();
  const router = useRouter();
  if (!selection) return <Redirect href="/picker" />;
  const { project, period } = selection;
  const changes = period.status === "changes_requested";

  return (
    <Screen>
      {!online ? <Banner tone="review" message={t("common.offline")} /> : null}
      <View style={styles.card}>
        <AppText variant="heading">{project.name}</AppText>
        <AppText variant="data" color={colors.textMuted}>
          {t("picker.period", {
            kind: period.kind,
            start: period.start_date,
            end: period.end_date,
          })}
        </AppText>
        <StatusPill
          tone={changes ? "review" : "ok"}
          label={t(changes ? "picker.status.changes_requested" : "picker.status.open")}
        />
      </View>
      <Button label={t("hub.capture")} onPress={() => router.push("/capture")} />
      <Button label={t("hub.measure")} onPress={() => router.push("/measure")} />
      <Button
        label={t("hub.outbox")}
        variant="secondary"
        detail={t("hub.outboxCount", { pending: counts.pending, failed: counts.failed })}
        onPress={() => router.push("/outbox")}
      />
      <Button label={t("hub.change")} variant="secondary" onPress={() => router.push("/picker")} />
      <Button label={t("common.signOut")} variant="secondary" onPress={() => void signOut()} />
    </Screen>
  );
}

const styles = StyleSheet.create({
  card: {
    gap: space.sm,
    padding: space.md,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
  },
});
