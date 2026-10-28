import { StyleSheet, TextInput, type TextInputProps, View } from "react-native";
import { colors, radius, space, touchTarget, type } from "../theme";
import { AppText } from "./AppText";

export interface TextFieldProps extends Omit<TextInputProps, "style"> {
  label: string;
  error?: string | undefined;
  numeric?: boolean;
}

export function TextField({ label, error, numeric = false, ...input }: TextFieldProps) {
  return (
    <View style={styles.wrap}>
      <AppText variant="label" color={colors.textMuted}>
        {label}
      </AppText>
      <TextInput
        accessibilityLabel={label}
        {...(error ? { accessibilityHint: error } : {})}
        placeholderTextColor={colors.textMuted}
        {...(numeric
          ? { keyboardType: "decimal-pad" as const, inputMode: "decimal" as const }
          : {})}
        {...input}
        style={[styles.input, numeric && type.data, error ? styles.invalid : null]}
      />
      {error ? (
        <AppText variant="label" color={colors.danger}>
          {error}
        </AppText>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: space.xs },
  input: {
    ...type.body,
    minHeight: touchTarget,
    borderWidth: 2,
    borderColor: colors.border,
    borderRadius: radius.sm,
    backgroundColor: colors.surface,
    color: colors.text,
    paddingHorizontal: space.md,
  },
  invalid: { borderColor: colors.danger },
});
