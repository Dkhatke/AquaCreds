/**
 * Outbox: every queued photo and measurement with its sync status and, once the server has
 * checked it, the trust score and each check's outcome and reason (as the verifier will see it).
 */
import { useCallback, useEffect, useState } from "react";
import { Alert, StyleSheet, View } from "react-native";
import { t } from "../src/i18n";
import type { CheckResult, OutboxItem, OutboxStatus } from "../src/outbox/types";
import { deleteFile } from "../src/platform/files";
import { useApp } from "../src/state/AppProvider";
import { colors, radius, type StatusTone, space } from "../src/theme";
import { AppText, Banner, Button, Screen, StatusPill } from "../src/ui";

const STATUS_TONE: Record<OutboxStatus, StatusTone> = {
  queued: "neutral",
  sending: "neutral",
  retry_wait: "review",
  awaiting_checks: "neutral",
  done: "ok",
  failed: "failed",
};

const OUTCOME_TONE: Record<CheckResult["outcome"], StatusTone> = {
  pass: "ok",
  warn: "review",
  fail: "failed",
  skipped: "neutral",
};

function resultTone(item: OutboxItem): StatusTone {
  if (item.result?.status === "rejected") return "failed";
  if (item.result?.checks.some((c) => c.outcome === "warn" || c.outcome === "fail")) {
    return "review";
  }
  return STATUS_TONE[item.status];
}

function ItemCard({
  item,
  onRetry,
  onRemove,
}: {
  item: OutboxItem;
  onRetry: () => void;
  onRemove: () => void;
}) {
  const title =
    item.kind === "photo"
      ? t("outbox.photo", { kind: t(`capture.kind.${item.payload.meta.kind}`) })
      : t("outbox.measurement", {
          plot: item.payload.plotCode,
          trees: item.payload.body.trees.length,
        });
  const result = item.result;
  return (
    <View style={styles.card}>
      <AppText variant="heading">{title}</AppText>
      <AppText variant="mono" color={colors.textMuted}>
        {new Date(item.createdAt).toLocaleString()}
        {item.kind === "photo" ? ` · sha256 ${item.payload.sha256.slice(0, 12)}…` : ""}
      </AppText>
      <View style={styles.row}>
        <StatusPill tone={resultTone(item)} label={t(`outbox.status.${item.status}`)} />
        {result ? (
          <StatusPill
            tone={result.status === "rejected" ? "failed" : "neutral"}
            label={t(`outbox.serverStatus.${result.status}`)}
          />
        ) : null}
      </View>
      {item.attempts > 0 ? (
        <AppText variant="label" color={colors.textMuted}>
          {t("outbox.attempts", { count: item.attempts })}
          {item.status === "retry_wait" && item.nextAttemptAt
            ? ` · ${t("outbox.nextTry", { when: new Date(item.nextAttemptAt).toLocaleTimeString() })}`
            : ""}
        </AppText>
      ) : null}
      {item.lastError && item.status !== "done" ? (
        <AppText
          variant="label"
          color={item.status === "failed" ? colors.danger : colors.warningInk}
        >
          {item.lastError}
        </AppText>
      ) : null}

      {result && result.trustScore !== null ? (
        <View style={styles.row}>
          <AppText variant="label" color={colors.textMuted}>
            {t("outbox.trust")}
          </AppText>
          <AppText variant="dataLarge">{result.trustScore}/100</AppText>
        </View>
      ) : null}
      {result?.checks.length ? (
        <View style={styles.checks}>
          <AppText variant="label" color={colors.textMuted}>
            {t("outbox.checks")}
          </AppText>
          {result.checks.map((c) => (
            <View key={c.name} style={styles.check}>
              <StatusPill tone={OUTCOME_TONE[c.outcome]} label={`${c.outcome} · ${c.name}`} />
              <AppText variant="label">{c.reason}</AppText>
            </View>
          ))}
        </View>
      ) : null}

      {item.status === "failed" || item.status === "retry_wait" ? (
        <View style={styles.actions}>
          <Button label={t("outbox.retry")} variant="secondary" onPress={onRetry} />
          {item.status === "failed" ? (
            <Button label={t("outbox.remove")} variant="danger" onPress={onRemove} />
          ) : null}
        </View>
      ) : null}
    </View>
  );
}

export default function Outbox() {
  const { outbox, outboxVersion, refreshOutbox, syncNow, syncing, online, session, lastSync } =
    useApp();
  const [items, setItems] = useState<OutboxItem[]>([]);

  const reload = useCallback(async () => setItems(await outbox.list()), [outbox]);
  // biome-ignore lint/correctness/useExhaustiveDependencies: reload whenever the outbox changes
  useEffect(() => {
    void reload();
  }, [reload, outboxVersion]);

  async function retry(item: OutboxItem) {
    await outbox.apply(item, { type: "retry" });
    await refreshOutbox();
    void syncNow();
  }

  function remove(item: OutboxItem) {
    Alert.alert(t("outbox.remove"), t("outbox.removeConfirm"), [
      { text: t("common.cancel"), style: "cancel" },
      {
        text: t("outbox.remove"),
        style: "destructive",
        onPress: () =>
          void (async () => {
            if (item.kind === "photo") {
              await deleteFile(item.payload.fileUri).catch(() => undefined);
            }
            await outbox.remove(item.id);
            await refreshOutbox();
          })(),
      },
    ]);
  }

  const open = items.filter((i) => i.status !== "done");
  const done = items.filter((i) => i.status === "done");
  return (
    <Screen
      footer={
        <Button
          label={syncing ? t("outbox.syncing") : t("outbox.syncNow")}
          onPress={() => void syncNow()}
          busy={syncing}
          disabled={!online || session !== "signedIn"}
        />
      }
    >
      {!online ? <Banner tone="review" message={t("common.offline")} /> : null}
      {session !== "signedIn" || lastSync?.needsSignIn ? (
        <Banner tone="review" message={t("outbox.signInToSync")} />
      ) : null}
      {open.length === 0 ? <AppText>{t("outbox.empty")}</AppText> : null}
      {[...open, ...done].map((item) => (
        <ItemCard
          key={item.id}
          item={item}
          onRetry={() => void retry(item)}
          onRemove={() => remove(item)}
        />
      ))}
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
  row: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: space.sm },
  checks: { gap: space.sm },
  check: { gap: space.xs },
  actions: { flexDirection: "row", gap: space.sm },
});
