import { Inter_400Regular, Inter_500Medium, Inter_600SemiBold } from "@expo-google-fonts/inter";
import { SpaceGrotesk_600SemiBold } from "@expo-google-fonts/space-grotesk";
import { useFonts } from "expo-font";
import { getLocales } from "expo-localization";
import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { ActivityIndicator, StyleSheet, View } from "react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { type StringKey, setLocale, t } from "../src/i18n";
import { AppProvider, useApp } from "../src/state/AppProvider";
import { colors, fonts, space } from "../src/theme";
import { AppText } from "../src/ui";

setLocale(getLocales()[0]?.languageTag ?? "en");

function Boot({ error }: { error: string | null }) {
  return (
    <View style={styles.boot}>
      {error ? (
        <AppText variant="body" color={colors.danger} accessibilityRole="alert">
          {error.startsWith("config.") ? t(error as StringKey) : error}
        </AppText>
      ) : (
        <ActivityIndicator
          size="large"
          color={colors.text}
          accessibilityLabel={t("common.loading")}
        />
      )}
    </View>
  );
}

function Routes() {
  const { session } = useApp();
  const signedIn = session === "signedIn";
  return (
    <Stack
      screenOptions={{
        headerStyle: { backgroundColor: colors.chrome },
        headerTintColor: colors.onChrome,
        headerTitleStyle: { fontFamily: fonts.display },
        contentStyle: { backgroundColor: colors.background },
      }}
    >
      <Stack.Protected guard={signedIn}>
        <Stack.Screen name="index" options={{ title: t("app.name") }} />
        <Stack.Screen name="picker" options={{ title: t("picker.title") }} />
        <Stack.Screen name="capture" options={{ title: t("capture.title") }} />
        <Stack.Screen name="measure" options={{ title: t("measure.title") }} />
      </Stack.Protected>
      <Stack.Protected guard={!signedIn}>
        <Stack.Screen name="sign-in" options={{ headerShown: false }} />
      </Stack.Protected>
      <Stack.Screen name="outbox" options={{ title: t("outbox.title") }} />
    </Stack>
  );
}

export default function RootLayout() {
  // Fonts are bundled with the app (no runtime download). On failure we fall back to system fonts.
  const [loaded, fontError] = useFonts({
    Inter_400Regular,
    Inter_500Medium,
    Inter_600SemiBold,
    SpaceGrotesk_600SemiBold,
  });
  return (
    <SafeAreaProvider>
      <StatusBar style="light" />
      {loaded || fontError ? (
        <AppProvider fallback={(error) => <Boot error={error} />}>
          <Routes />
        </AppProvider>
      ) : (
        <Boot error={null} />
      )}
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  boot: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: space.lg,
    backgroundColor: colors.background,
  },
});
