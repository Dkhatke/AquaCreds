import { cookies } from "next/headers";
import { getRequestConfig } from "next-intl/server";
import { DEFAULT_LOCALE, isLocale, LOCALE_COOKIE, type Locale, mergeMessages } from "./config";

async function load(locale: Locale) {
  return (await import(`../../messages/${locale}.json`)).default;
}

/**
 * Locale comes from a cookie, not the URL: URLs, the auth proxy matcher and demo links stay
 * unchanged. English is always loaded and the chosen language is laid over it, so an untranslated
 * string renders in English instead of as a key.
 */
export default getRequestConfig(async () => {
  const raw = (await cookies()).get(LOCALE_COOKIE)?.value;
  const locale: Locale = isLocale(raw) ? raw : DEFAULT_LOCALE;
  const en = await load("en");
  const messages = locale === "en" ? en : mergeMessages(en, await load(locale));
  return { locale, messages };
});
