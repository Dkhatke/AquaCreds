"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useId, useState } from "react";
import { Button, ErrorState } from "@/components/ui";
import { errorMessage, postBff } from "@/lib/api-browser";
import { safeNext } from "@/lib/safe-next";

type Step = "password" | "mfa";

const input =
  "mt-1 block h-11 w-full rounded-lg border border-line bg-deep-ocean px-3 text-fg placeholder:text-fg-muted/70";

/** Two-step sign-in: password, then a TOTP / recovery code when the account has MFA. */
export function LoginForm({ next, expired }: { next: string | null; expired: boolean }) {
  const router = useRouter();
  const ids = { email: useId(), password: useId(), code: useId() };
  const [step, setStep] = useState<Step>("password");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const done = (recoveryLeft?: number | null) => {
    if (typeof recoveryLeft === "number") {
      setNotice(`Signed in with a recovery code. ${recoveryLeft} left: regenerate them soon.`);
    }
    router.replace(safeNext(next));
    router.refresh();
  };

  const onPassword = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    setBusy(true);
    setError(null);
    try {
      const res = await postBff<{ ok?: true; mfa_required?: true }>("/bff/auth/login", {
        email: String(form.get("email") ?? ""),
        password: String(form.get("password") ?? ""),
      });
      if (res.mfa_required) setStep("mfa");
      else done();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const onCode = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    setBusy(true);
    setError(null);
    try {
      const res = await postBff<{ ok: true; recovery_codes_remaining: number | null }>(
        "/bff/auth/mfa",
        {
          code: String(form.get("code") ?? "").replace(/\s+/g, ""),
        },
      );
      done(res.recovery_codes_remaining);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="w-full max-w-sm">
      {expired && step === "password" && (
        <p
          role="status"
          className="mb-4 rounded-lg border border-review-amber/60 px-3 py-2 text-sm text-warn"
        >
          Your session ended. Sign in again to continue.
        </p>
      )}
      {step === "password" ? (
        <form
          key="password"
          onSubmit={onPassword}
          className="space-y-4"
          aria-describedby={error ? "login-error" : undefined}
        >
          <div>
            <label htmlFor={ids.email} className="text-sm font-medium">
              Email
            </label>
            <input
              id={ids.email}
              name="email"
              type="email"
              autoComplete="username"
              required
              className={input}
            />
          </div>
          <div>
            <label htmlFor={ids.password} className="text-sm font-medium">
              Password
            </label>
            <input
              id={ids.password}
              name="password"
              type="password"
              autoComplete="current-password"
              required
              className={input}
            />
          </div>
          <Button type="submit" size="lg" className="w-full" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </Button>
        </form>
      ) : (
        <form key="mfa" onSubmit={onCode} className="space-y-4">
          <p className="text-sm text-fg-muted">
            This account uses two-factor authentication. Enter the 6-digit code from your
            authenticator app, or one of your recovery codes.
          </p>
          <div>
            <label htmlFor={ids.code} className="text-sm font-medium">
              Authentication code
            </label>
            <input
              id={ids.code}
              name="code"
              inputMode="text"
              autoComplete="one-time-code"
              required
              minLength={6}
              maxLength={32}
              className={`${input} tabular tracking-widest`}
              // biome-ignore lint/a11y/noAutofocus: the code field is the only task on this step.
              autoFocus
            />
          </div>
          <Button type="submit" size="lg" className="w-full" disabled={busy}>
            {busy ? "Verifying…" : "Verify"}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setStep("password");
              setError(null);
            }}
          >
            Use a different account
          </Button>
        </form>
      )}
      {error && (
        <div id="login-error" className="mt-4">
          <ErrorState title="Sign-in failed" error={error} />
        </div>
      )}
      {notice && (
        <p role="status" className="mt-4 text-sm text-warn">
          {notice}
        </p>
      )}
    </div>
  );
}
