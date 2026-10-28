import { StyleSheet, View } from "react-native";
import { radius, type StatusTone, space, tones } from "../theme";
import { AppText } from "./AppText";

export function StatusPill({ tone, label }: { tone: StatusTone; label: string }) {
  const t = tones[tone];
  return (
    <View style={[styles.pill, { backgroundColor: t.fill }]}>
      <AppText variant="label" color={t.text}>
        {label}
      </AppText>
    </View>
  );
}

const styles = StyleSheet.create({
  pill: {
    alignSelf: "flex-start",
    borderRadius: radius.lg,
    paddingHorizontal: space.sm + 4,
    paddingVertical: space.xs,
  },
});
