import { ApiError, type MfaChallenge } from "@aquacreds/api-client";
import { useState } from "react";
import { StyleSheet, View } from "react-native";
import { t } from "../src/i18n";
import { detailMessage } from "../src/outbox/machine";
import { useApp } from "../src/state/AppProvider";
import { colors, space } from "../src/theme";
import { AppText, Banner, Button, Screen, TextField } from "../src/ui";

function message(err: unknown): string {
  if (err instanceof ApiError) return detailMessage(err);
  if (err instanceof Error) return err.message;
  return String(err);
}

export default function SignIn() {
  const { client, setSignedIn, online } = useApp();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [challenge, setChallenge] = useState<MfaChallenge | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function run(step: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await step();
    } catch (err) {
      setError(t("signIn.failed", { message: message(err) }));
    } finally {
      setBusy(false);
    }
  }

  const submitPassword = () =>
    run(async () => {
      const res = await client.auth.login(email.trim(), password);
      if ("mfa_required" in res) {
        setChallenge(res);
        return;
      }
      setPassword("");
      setSignedIn();
    });

  const submitCode = () =>
    run(async () => {
      if (!challenge) return;
      const pair = await client.auth.verifyMfa(challenge.mfa_token, code.trim());
      if (pair.recovery_codes_remaining != null) {
        setNotice(t("signIn.recoveryLow", { count: pair.recovery_codes_remaining }));
      }
      setPassword("");
      setSignedIn();
    });

  return (
    <Screen>
      <View style={styles.header}>
        <AppText variant="title" accessibilityRole="header">
          {t("app.name")}
        </AppText>
        <AppText color={colors.textMuted}>{t("signIn.subtitle")}</AppText>
      </View>
      {!online ? <Banner tone="review" message={t("common.offline")} /> : null}
      {error ? <Banner tone="failed" message={error} /> : null}
      {notice ? <Banner tone="review" message={notice} /> : null}

      {challenge ? (
        <>
          <AppText variant="heading">{t("signIn.mfaTitle")}</AppText>
          <AppText color={colors.textMuted}>{t("signIn.mfaHint")}</AppText>
          <TextField
            label={t("signIn.mfaCode")}
            value={code}
            onChangeText={setCode}
            autoComplete="one-time-code"
            textContentType="oneTimeCode"
            autoCapitalize="none"
            autoFocus
          />
          <Button
            label={t("signIn.mfaSubmit")}
            onPress={submitCode}
            busy={busy}
            disabled={!code.trim()}
          />
          <Button
            label={t("common.back")}
            variant="secondary"
            onPress={() => {
              setChallenge(null);
              setCode("");
            }}
          />
        </>
      ) : (
        <>
          <AppText variant="heading">{t("signIn.title")}</AppText>
          <TextField
            label={t("signIn.email")}
            value={email}
            onChangeText={setEmail}
            autoComplete="email"
            keyboardType="email-address"
            autoCapitalize="none"
            autoCorrect={false}
          />
          <TextField
            label={t("signIn.password")}
            value={password}
            onChangeText={setPassword}
            secureTextEntry
            autoComplete="password"
          />
          <Button
            label={t("signIn.submit")}
            onPress={submitPassword}
            busy={busy}
            disabled={!email.trim() || !password || !online}
          />
        </>
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  header: { gap: space.xs, paddingTop: space.xl },
});
