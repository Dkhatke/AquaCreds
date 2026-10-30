export const LOCALES = ["en", "hi", "or"] as const;
export type Locale = (typeof LOCALES)[number];
export const DEFAULT_LOCALE: Locale = "en";
/** Persisted language choice. Not a credential: readable by the browser, one year. */
export const LOCALE_COOKIE = "aq_locale";

/** Native names, shown in the switcher so a reader can always find their own language. */
export const LOCALE_NAMES: Record<Locale, string> = {
  en: "English",
  hi: "हिन्दी",
  or: "ଓଡ଼ିଆ",
};

/** BCP 47 tag for the `lang` attribute and Intl formatting. */
export const LOCALE_TAG: Record<Locale, string> = { en: "en", hi: "hi", or: "or" };

export function isLocale(value: string | undefined | null): value is Locale {
  return (LOCALES as readonly string[]).includes(value ?? "");
}

type Messages = { [key: string]: string | Messages };

/** Overlay `over` onto `base`: any key a translation lacks falls back to English. */
export function mergeMessages(base: Messages, over: Messages): Messages {
  const out: Messages = { ...base };
  for (const [k, v] of Object.entries(over)) {
    if (k.startsWith("_")) continue; // _meta: review status, not UI text
    const b = out[k];
    if (typeof v === "string") {
      if (v.trim() !== "") out[k] = v;
    } else if (typeof b === "object" && b !== null) {
      out[k] = mergeMessages(b, v);
    } else {
      out[k] = mergeMessages({}, v);
    }
  }
  return out;
}
