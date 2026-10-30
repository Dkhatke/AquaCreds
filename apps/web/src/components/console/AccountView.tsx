"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";
import { Button, Card, ErrorState, LoadingState, StatusPill } from "@/components/ui";
import { errorMessage } from "@/lib/api-browser";
import { formatDate } from "@/lib/format";
import { useMfaStatus, useRegenerateRecoveryCodes } from "@/lib/overview-queries";

export const LOW_CODES = 3;

export function AccountView() {
  const t = useTranslations("account");
  const mfa = useMfaStatus();
  return (
    <div className="mx-auto max-w-3xl space-y-6 p-4 md:p-8">
      <div>
        <h1 className="font-display text-3xl font-semibold">{t("title")}</h1>
        <p className="mt-2 max-w-2xl text-fg-muted">{t("intro")}</p>
      </div>
      <Card title={t("mfa.title")} meta="GET /api/v1/auth/mfa">
        {mfa.isPending ? (
          <LoadingState label={t("mfa.title")} />
        ) : mfa.isError ? (
          <ErrorState error={mfa.error} onRetry={() => void mfa.refetch()} />
        ) : (
          <div className="space-y-2">
            <StatusPill status={mfa.data.enabled ? "ok" : "review"}>
              {mfa.data.enabled ? t("mfa.enabled") : t("mfa.disabled")}
            </StatusPill>
            {mfa.data.enabled_at && (
              <p className="text-sm text-fg-muted">
                {t("mfa.since", { date: formatDate(mfa.data.enabled_at) })}
              </p>
            )}
            <p className="text-sm text-fg-muted">
              {mfa.data.required ? t("mfa.required") : t("mfa.notRequired")}
            </p>
            {!mfa.data.enabled && <p className="text-sm text-fg-muted">{t("mfa.setupMissing")}</p>}
          </div>
        )}
      </Card>
      {mfa.data?.enabled && <RecoveryCodes remaining={mfa.data.recovery_codes_remaining} />}
    </div>
  );
}

function RecoveryCodes({ remaining }: { remaining: number }) {
  const t = useTranslations("account.codes");
  const regen = useRegenerateRecoveryCodes();
  const [code, setCode] = useState("");
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const fresh = regen.data?.recovery_codes ?? null;
  const low = remaining <= LOW_CODES;

  return (
    <Card title={t("title")} meta="POST /api/v1/auth/mfa/recovery-codes">
      <p className="text-sm">{t("remaining", { count: remaining })}</p>
      {low && !fresh && (
        <p
          role="status"
          className="mt-3 rounded-lg border border-review-amber/60 px-3 py-2 text-sm text-warn"
        >
          {remaining === 0 ? t("none") : t("low", { count: remaining })}
        </p>
      )}

      {fresh ? (
        <div className="mt-4">
          <h3 className="text-sm font-semibold">{t("newCodes")}</h3>
          <p className="mt-1 text-sm text-fg-muted">{t("shownOnce")}</p>
          <ul className="tabular mt-3 grid gap-1 rounded-lg bg-deep-ocean p-3 font-mono text-sm sm:grid-cols-2">
            {fresh.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
          <div className="mt-3 flex gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                void navigator.clipboard
                  .writeText(fresh.join("\n"))
                  .then(() => setCopied(true))
                  .catch(() => setCopied(false));
              }}
            >
              {copied ? t("copied") : t("copy")}
            </Button>
            <Button
              size="sm"
              onClick={() => {
                regen.reset();
                setOpen(false);
                setCode("");
                setCopied(false);
              }}
            >
              {t("done")}
            </Button>
          </div>
        </div>
      ) : open ? (
        <form
          className="mt-4 space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            regen.mutate(code.trim());
          }}
        >
          <div>
            <label htmlFor="totp" className="block text-sm font-medium">
              {t("totpLabel")}
            </label>
            <input
              id="totp"
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="[0-9]{6}"
              maxLength={6}
              required
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
              className="tabular mt-1 h-10 w-40 rounded-lg border border-line bg-deep-ocean px-3 text-base tracking-widest"
            />
            <p className="mt-1 text-xs text-fg-muted">{t("totpHelp")}</p>
          </div>
          {regen.isError && (
            <p role="alert" className="text-sm text-fail">
              {errorMessage(regen.error)}
            </p>
          )}
          <div className="flex gap-2">
            <Button type="submit" size="sm" disabled={regen.isPending || code.length !== 6}>
              {t("confirm")}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setOpen(false);
                setCode("");
                regen.reset();
              }}
            >
              {t("cancel")}
            </Button>
          </div>
        </form>
      ) : (
        <Button variant="secondary" size="sm" className="mt-4" onClick={() => setOpen(true)}>
          {t("regenerate")}
        </Button>
      )}
    </Card>
  );
}
