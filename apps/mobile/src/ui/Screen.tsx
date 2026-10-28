import type { ReactNode } from "react";
import { ScrollView, StyleSheet, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { colors, space } from "../theme";
import { AppText } from "./AppText";

export function Screen({
  title,
  children,
  scroll = true,
  footer,
}: {
  title?: string;
  children: ReactNode;
  scroll?: boolean;
  footer?: ReactNode;
}) {
  const body = (
    <>
      {title ? (
        <AppText variant="title" accessibilityRole="header">
          {title}
        </AppText>
      ) : null}
      {children}
    </>
  );
  return (
    <SafeAreaView style={styles.safe} edges={["top", "bottom", "left", "right"]}>
      {scroll ? (
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          {body}
        </ScrollView>
      ) : (
        <View style={[styles.content, styles.fill]}>{body}</View>
      )}
      {footer ? <View style={styles.footer}>{footer}</View> : null}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: colors.background },
  content: { padding: space.md, gap: space.md },
  fill: { flex: 1 },
  footer: {
    padding: space.md,
    gap: space.sm,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    backgroundColor: colors.surface,
  },
});
