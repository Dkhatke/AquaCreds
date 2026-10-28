import { StyleSheet, View } from "react-native";
import { radius, type StatusTone, space, tones } from "../theme";
import { AppText } from "./AppText";

/** A full-width status message. Colour is never the only signal: the text says what is wrong. */
export function Banner({ tone, message }: { tone: StatusTone; message: string }) {
  const t = tones[tone];
  return (
    <View
      accessibilityRole={tone === "failed" ? "alert" : "text"}
      style={[styles.box, { backgroundColor: t.fill }]}
    >
      <AppText variant="label" color={t.text}>
        {message}
      </AppText>
    </View>
  );
}

const styles = StyleSheet.create({
  box: { borderRadius: radius.sm, padding: space.md },
});
