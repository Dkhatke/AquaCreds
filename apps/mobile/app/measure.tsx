/**
 * Sample-plot measurement (AR-TOOL14 ex-post biomass): per-tree species, DBH, height and alive/dead.
 * Validated on the phone with the API's rules, then queued in the outbox (works offline).
 */
import { useRouter } from "expo-router";
import { useEffect, useMemo, useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { searchSpecies } from "../src/domain/species";
import {
  emptyTree,
  type FieldError,
  localIsoDate,
  type MeasurementDraft,
  type MeasurementErrors,
  type TreeDraft,
  validateMeasurement,
} from "../src/domain/validation";
import { t } from "../src/i18n";
import { type PlotOut, useApp } from "../src/state/AppProvider";
import { loadPlots } from "../src/state/fieldData";
import { colors, radius, space, touchTarget } from "../src/theme";
import { AppText, Banner, Button, Choice, Screen, TextField } from "../src/ui";

const msg = (e: FieldError | undefined) => (e ? t(e.key, e.params) : undefined);

function SpeciesField({
  value,
  error,
  onChange,
}: {
  value: string;
  error: string | undefined;
  onChange: (v: string) => void;
}) {
  const [focused, setFocused] = useState(false);
  const suggestions = useMemo(() => searchSpecies(value, 6), [value]);
  const exact = suggestions.some((s) => s.value === value);
  return (
    <View style={styles.species}>
      <TextField
        label={t("measure.species")}
        value={value}
        onChangeText={onChange}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        autoCapitalize="words"
        autoCorrect={false}
        error={error}
      />
      {focused && !exact
        ? suggestions.map((s) => (
            <Pressable
              key={s.value}
              accessibilityRole="button"
              onPress={() => onChange(s.value)}
              style={styles.suggestion}
            >
              <AppText>{s.value}</AppText>
            </Pressable>
          ))
        : null}
    </View>
  );
}

export default function Measure() {
  const { client, cache, outbox, online, selection, refreshOutbox, syncNow } = useApp();
  const router = useRouter();
  const [plots, setPlots] = useState<PlotOut[]>([]);
  const [draft, setDraft] = useState<MeasurementDraft>(() => ({
    plotId: "",
    measuredOn: localIsoDate(new Date()),
    trees: [emptyTree()],
    notes: "",
  }));
  const [errors, setErrors] = useState<MeasurementErrors | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const projectId = selection?.project.id;

  useEffect(() => {
    if (!projectId) return;
    loadPlots(client, cache, projectId, online)
      .then((r) => setPlots(r.value))
      .catch(() => undefined);
  }, [client, cache, projectId, online]);

  if (!selection || !projectId) return null;
  const { period } = selection;

  const setTree = (i: number, patch: Partial<TreeDraft>) =>
    setDraft((d) => ({
      ...d,
      trees: d.trees.map((tr, j) => (j === i ? { ...tr, ...patch } : tr)),
    }));

  async function save() {
    const result = validateMeasurement(draft, period, new Date());
    if (!result.ok) {
      setErrors(result.errors);
      return;
    }
    const plot = plots.find((p) => p.id === draft.plotId);
    if (!plot || !projectId) return;
    setErrors(null);
    setSaving(true);
    setSaveError(null);
    try {
      await outbox.add({
        kind: "measurement",
        projectId,
        periodId: period.id,
        payload: { plotId: plot.id, plotCode: plot.code, body: result.body },
      });
      await refreshOutbox();
      void syncNow();
      router.back();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Screen
      footer={
        <>
          {errors ? <Banner tone="failed" message={t("measure.fixErrors")} /> : null}
          <Button label={t("measure.submit")} onPress={() => void save()} busy={saving} />
        </>
      }
    >
      {saveError ? <Banner tone="failed" message={saveError} /> : null}
      {plots.length === 0 ? (
        <Banner tone="review" message={t("measure.noPlots")} />
      ) : (
        <Choice<string>
          label={t("measure.plot")}
          value={draft.plotId || null}
          onChange={(plotId) => setDraft((d) => ({ ...d, plotId }))}
          options={plots.map((p) => ({ value: p.id, label: `${p.code} · ${p.stratum}` }))}
        />
      )}
      {errors?.plotId ? (
        <AppText variant="label" color={colors.danger}>
          {msg(errors.plotId)}
        </AppText>
      ) : null}

      <TextField
        label={t("measure.date")}
        value={draft.measuredOn}
        onChangeText={(measuredOn) => setDraft((d) => ({ ...d, measuredOn }))}
        error={msg(errors?.measuredOn)}
        keyboardType="numbers-and-punctuation"
      />

      {draft.trees.map((tree, i) => {
        const te = errors?.tree[i];
        return (
          // biome-ignore lint/suspicious/noArrayIndexKey: rows have no identity besides position
          <View key={i} style={styles.tree}>
            <AppText variant="heading">{t("measure.tree", { n: i + 1 })}</AppText>
            <SpeciesField
              value={tree.species}
              error={msg(te?.species)}
              onChange={(species) => setTree(i, { species })}
            />
            <View style={styles.row}>
              <View style={styles.cell}>
                <TextField
                  label={t("measure.dbh")}
                  value={tree.dbhCm}
                  onChangeText={(dbhCm) => setTree(i, { dbhCm })}
                  error={msg(te?.dbhCm)}
                  numeric
                />
              </View>
              <View style={styles.cell}>
                <TextField
                  label={t("measure.height")}
                  value={tree.heightM}
                  onChangeText={(heightM) => setTree(i, { heightM })}
                  error={msg(te?.heightM)}
                  numeric
                />
              </View>
            </View>
            <Choice<"alive" | "dead">
              label={t("measure.condition")}
              value={tree.alive ? "alive" : "dead"}
              onChange={(v) => setTree(i, { alive: v === "alive" })}
              options={[
                { value: "alive", label: t("measure.alive") },
                { value: "dead", label: t("measure.dead") },
              ]}
            />
            {draft.trees.length > 1 ? (
              <Button
                label={t("measure.removeTree")}
                variant="secondary"
                onPress={() =>
                  setDraft((d) => ({ ...d, trees: d.trees.filter((_, j) => j !== i) }))
                }
              />
            ) : null}
          </View>
        );
      })}
      {errors?.trees ? (
        <AppText variant="label" color={colors.danger}>
          {msg(errors.trees)}
        </AppText>
      ) : null}
      <Button
        label={t("measure.addTree")}
        variant="secondary"
        onPress={() => setDraft((d) => ({ ...d, trees: [...d.trees, emptyTree()] }))}
      />
      <TextField
        label={t("measure.notes")}
        value={draft.notes}
        onChangeText={(notes) => setDraft((d) => ({ ...d, notes }))}
        error={msg(errors?.notes)}
        multiline
      />
    </Screen>
  );
}

const styles = StyleSheet.create({
  tree: {
    gap: space.sm,
    padding: space.md,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
  },
  row: { flexDirection: "row", gap: space.sm },
  cell: { flex: 1 },
  species: { gap: space.xs },
  suggestion: {
    minHeight: touchTarget - 8,
    justifyContent: "center",
    paddingHorizontal: space.md,
    borderRadius: radius.sm,
    backgroundColor: colors.surfaceMuted,
  },
});
