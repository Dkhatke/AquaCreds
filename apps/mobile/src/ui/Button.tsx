import { ActivityIndicator, Pressable, StyleSheet, View } from "react-native";
import { colors, radius, space, touchTarget } from "../theme";
import { AppText } from "./AppText";

type Variant = "primary" | "secondary" | "danger";

export interface ButtonProps {
  label: string;
  onPress: () => void;
  variant?: Variant;
  disabled?: boolean;
  busy?: boolean;
  /** Secondary line under the label (e.g. a count). */
  detail?: string;
  accessibilityHint?: string;
}

const fills: Record<Variant, { bg: string; fg: string; border: string }> = {
  primary: { bg: colors.primary, fg: colors.onPrimary, border: colors.primary },
  secondary: { bg: colors.surface, fg: colors.text, border: colors.text },
  danger: { bg: colors.danger, fg: colors.onDanger, border: colors.danger },
};

export function Button({
  label,
  onPress,
  variant = "primary",
  disabled = false,
  busy = false,
  detail,
  accessibilityHint,
}: ButtonProps) {
  const f = fills[variant];
  const inactive = disabled || busy;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={detail ? `${label}. ${detail}` : label}
      accessibilityState={{ disabled: inactive, busy }}
      {...(accessibilityHint ? { accessibilityHint } : {})}
      disabled={inactive}
      onPress={onPress}
      hitSlop={8}
      style={({ pressed }) => [
        styles.base,
        { backgroundColor: f.bg, borderColor: f.border },
        inactive && styles.inactive,
        pressed && styles.pressed,
      ]}
    >
      <View style={styles.row}>
        {busy ? <ActivityIndicator color={f.fg} /> : null}
        <AppText variant="button" color={f.fg}>
          {label}
        </AppText>
      </View>
      {detail ? (
        <AppText variant="label" color={f.fg}>
          {detail}
        </AppText>
      ) : null}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  base: {
    minHeight: touchTarget,
    borderRadius: radius.md,
    borderWidth: 2,
    paddingHorizontal: space.md,
    paddingVertical: space.sm,
    alignItems: "center",
    justifyContent: "center",
  },
  row: { flexDirection: "row", alignItems: "center", gap: space.sm },
  inactive: { opacity: 0.45 },
  pressed: { opacity: 0.8 },
});
