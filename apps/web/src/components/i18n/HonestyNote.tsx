import { useLocale, useTranslations } from "next-intl";
import { cn } from "@/lib/cn";

export type HonestyKind = "carbon" | "estimated" | "contextOnly" | "demo";

/**
 * Carbon / legal honesty label. The English original is always shown (it is the authoritative
 * wording); in another language the translation is added beneath it, never in place of it.
 * `honesty.*` is never overridden by hi/or; their gloss lives under `honestyLocal.*`.
 */
export function HonestyNote({
  kind,
  className,
  as: Tag = "p",
}: {
  kind: HonestyKind;
  className?: string;
  as?: "p" | "span" | "div";
}) {
  const t = useTranslations();
  const locale = useLocale();
  const local = locale !== "en" && t.has(`honestyLocal.${kind}`) ? t(`honestyLocal.${kind}`) : null;
  return (
    <Tag className={cn(className)}>
      <span lang="en">{t(`honesty.${kind}`)}</span>
      {local && (
        <>
          {" "}
          <span lang={locale} className="block opacity-90">
            {local}
          </span>
        </>
      )}
    </Tag>
  );
}
