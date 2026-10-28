import { Pressable, StyleSheet, View } from "react-native";
import { colors, radius, space, touchTarget } from "../theme";
import { AppText } from "./AppText";

export interface ChoiceOption<T extends string> {
  value: T;
  label: string;
}

/** Large segmented choice (radio group). */
export function Choice<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: ChoiceOption<T>[];
  value: T | null;
  onChange: (v: T) => void;
}) {
  return (
    <View style={styles.wrap} accessibilityRole="radiogroup" accessibilityLabel={label}>
      <AppText variant="label" color={colors.textMuted}>
        {label}
      </AppText>
      <View style={styles.row}>
        {options.map((o) => {
          const selected = o.value === value;
          return (
            <Pressable
              key={o.value}
              accessibilityRole="radio"
              accessibilityState={{ selected }}
              onPress={() => onChange(o.value)}
              style={[styles.option, selected && styles.selected]}
            >
              <AppText variant="label" color={selected ? colors.onPrimary : colors.text}>
                {o.label}
              </AppText>
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: space.xs },
  row: { flexDirection: "row", flexWrap: "wrap", gap: space.sm },
  option: {
    minHeight: touchTarget,
    minWidth: touchTarget * 1.5,
    paddingHorizontal: space.md,
    borderRadius: radius.sm,
    borderWidth: 2,
    borderColor: colors.text,
    backgroundColor: colors.surface,
    alignItems: "center",
    justifyContent: "center",
  },
  selected: { backgroundColor: colors.primary, borderColor: colors.primary },
});
