import { useLocale, useTranslations } from "next-intl";
import { cn } from "@/lib/cn";

/**
 * Shown whenever a non-English language is active: the translations are AI-drafted and have not
 * been reviewed by a native speaker. English first (authoritative), the local text beneath it.
 */
export function TranslationNotice({ className }: { className?: string }) {
  const t = useTranslations();
  const locale = useLocale();
  if (locale === "en") return null;
  return (
    <p className={cn("text-[11px] leading-snug text-fg-muted", className)}>
      <span lang="en">{t("language.reviewNotice")}</span>
      {t.has("languageLocal.reviewNotice") && (
        <span lang={locale} className="mt-1 block">
          {t("languageLocal.reviewNotice")}
        </span>
      )}
    </p>
  );
}
