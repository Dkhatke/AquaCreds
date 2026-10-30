import { describe, expect, it } from "vitest";
import en from "../../messages/en.json";
import hi from "../../messages/hi.json";
import or from "../../messages/or.json";
import { isLocale, LOCALES, mergeMessages } from "./config";

type Tree = { [k: string]: string | Tree };

function leaves(tree: Tree, prefix = ""): string[] {
  return Object.entries(tree).flatMap(([k, v]) =>
    typeof v === "string" ? [`${prefix}${k}`] : leaves(v, `${prefix}${k}.`),
  );
}
const enKeys = new Set(leaves(en as unknown as Tree));

describe("message catalogs", () => {
  it("lists exactly the supported locales", () => {
    expect([...LOCALES]).toEqual(["en", "hi", "or"]);
    expect(isLocale("hi")).toBe(true);
    expect(isLocale("fr")).toBe(false);
    expect(isLocale(undefined)).toBe(false);
  });

  for (const [name, cat] of [
    ["hi", hi],
    ["or", or],
  ] as const) {
    it(`${name}.json is marked AI-drafted and unreviewed`, () => {
      const meta = (cat as unknown as { _meta: { status: string; reviewed: boolean } })._meta;
      expect(meta.status).toBe("ai-drafted");
      expect(meta.reviewed).toBe(false);
    });

    it(`${name}.json only overrides keys that exist in English (plus the local honesty gloss)`, () => {
      const { _meta, ...rest } = cat as unknown as Tree;
      void _meta;
      const stray = leaves(rest).filter(
        (k) => !enKeys.has(k) && !k.startsWith("honestyLocal.") && !k.startsWith("languageLocal."),
      );
      expect(stray).toEqual([]);
    });

    it(`${name}.json never replaces the English honesty labels`, () => {
      const keys = leaves(cat as unknown as Tree);
      expect(keys.filter((k) => k.startsWith("honesty.") || k === "language.reviewNotice")).toEqual(
        [],
      );
    });

    it(`${name}.json translates the hero, nav and console navigation`, () => {
      const keys = new Set(leaves(cat as unknown as Tree));
      for (const k of [
        "hero.title",
        "hero.body",
        "hero.ctaDemo",
        "siteNav.platform",
        "console.nav.overview",
        "console.nav.community",
        "console.nav.notifications",
        "honestyLocal.carbon",
      ]) {
        expect(keys.has(k), k).toBe(true);
      }
    });

    it(`${name}.json keeps ICU placeholders intact`, () => {
      const lookup = (tree: Tree, key: string) =>
        key
          .split(".")
          .reduce<string | Tree | undefined>(
            (t, k) => (typeof t === "object" ? t[k] : undefined),
            tree,
          );
      for (const key of leaves(cat as unknown as Tree)) {
        const en_ = lookup(en as unknown as Tree, key);
        const tr = lookup(cat as unknown as Tree, key);
        if (typeof en_ !== "string" || typeof tr !== "string") continue;
        const ph = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
        expect(ph(tr), key).toEqual(ph(en_));
      }
    });
  }
});

describe("mergeMessages", () => {
  it("falls back to English for anything untranslated and ignores _meta", () => {
    const base = { a: "A", b: { c: "C", d: "D" } };
    const merged = mergeMessages(base, { _meta: { x: "y" }, b: { c: "सी" }, a: "  " });
    expect(merged).toEqual({ a: "A", b: { c: "सी", d: "D" } });
  });

  it("gives every key of the real catalogs a value in each locale", () => {
    for (const cat of [hi, or]) {
      const merged = mergeMessages(en as unknown as Tree, cat as unknown as Tree);
      const keys = new Set(leaves(merged as Tree));
      for (const k of enKeys) expect(keys.has(k), k).toBe(true);
    }
  });
});
