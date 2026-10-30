"use client";

import { useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { useId, useTransition } from "react";
import { isLocale, LOCALE_COOKIE, LOCALE_NAMES, LOCALES } from "@/i18n/config";
import { cn } from "@/lib/cn";

const ONE_YEAR = 60 * 60 * 24 * 365;

/** Persist the choice in a cookie (read by the server on the next render) and refresh. */
export function LanguageSwitcher({ className }: { className?: string }) {
  const t = useTranslations("language");
  const locale = useLocale();
  const router = useRouter();
  const id = useId();
  const [pending, start] = useTransition();

  const change = (value: string) => {
    if (!isLocale(value)) return;
    const secure = window.location.protocol === "https:" ? "; Secure" : "";
    // biome-ignore lint/suspicious/noDocumentCookie: a plain, non-sensitive preference cookie.
    document.cookie = `${LOCALE_COOKIE}=${value}; Path=/; Max-Age=${ONE_YEAR}; SameSite=Lax${secure}`;
    start(() => router.refresh());
  };

  return (
    <div className={cn("flex items-center gap-1.5", className)}>
      <label htmlFor={id} className="sr-only">
        {t("label")}
      </label>
      <select
        id={id}
        value={locale}
        disabled={pending}
        onChange={(e) => change(e.target.value)}
        className="h-8 rounded-full border border-line bg-transparent px-2 text-sm text-fg"
      >
        {LOCALES.map((l) => (
          <option key={l} value={l} lang={l} className="bg-deep-ocean text-fg">
            {LOCALE_NAMES[l]}
          </option>
        ))}
      </select>
    </div>
  );
}
