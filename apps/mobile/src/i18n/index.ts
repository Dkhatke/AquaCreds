import { en, type StringKey } from "./en";

export type { StringKey };
export type Params = Record<string, string | number>;

type Dictionary = Record<StringKey, string>;

// English first. Add a locale by adding a complete dictionary here (keys are type-checked).
const dictionaries: Record<string, Dictionary> = { en };
let active: Dictionary = en;

export function setLocale(languageTag: string): void {
  const lang = languageTag.toLowerCase().split("-")[0] ?? "en";
  active = dictionaries[lang] ?? en;
}

/** Translate `key`, filling `{name}` placeholders. Unknown placeholders are left visible. */
export function t(key: StringKey, params?: Params): string {
  const template = active[key];
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in params ? String(params[name]) : match,
  );
}
