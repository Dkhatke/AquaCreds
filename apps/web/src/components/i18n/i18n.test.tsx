import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { mergeMessages } from "@/i18n/config";
import en from "../../../messages/en.json";
import hi from "../../../messages/hi.json";
import { HonestyNote } from "./HonestyNote";
import { LanguageSwitcher } from "./LanguageSwitcher";
import { TranslationNotice } from "./TranslationNotice";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));

const wrap = (locale: "en" | "hi", ui: React.ReactNode) =>
  render(
    <NextIntlClientProvider locale={locale} messages={locale === "en" ? en : mergeMessages(en, hi)}>
      {ui}
    </NextIntlClientProvider>,
  );

describe("HonestyNote", () => {
  it("shows English only in English", () => {
    wrap("en", <HonestyNote kind="carbon" />);
    expect(
      screen.getByText(/Carbon figures are estimated, pending accredited verification/),
    ).toBeVisible();
    expect(screen.queryByText(/कार्बन/)).toBeNull();
  });

  it("keeps the English original visible next to the Hindi gloss", () => {
    wrap("hi", <HonestyNote kind="carbon" />);
    const english = screen.getByText(
      /Carbon figures are estimated, pending accredited verification/,
    );
    expect(english).toHaveAttribute("lang", "en");
    expect(screen.getByText(/कार्बन के आँकड़े अनुमानित हैं/)).toHaveAttribute("lang", "hi");
  });
});

describe("TranslationNotice", () => {
  it("renders nothing in English and the review warning otherwise", () => {
    const { container } = wrap("en", <TranslationNotice />);
    expect(container).toBeEmptyDOMElement();
    wrap("hi", <TranslationNotice />);
    expect(screen.getByText(/AI-drafted translation that has not yet been reviewed/)).toBeVisible();
  });
});

describe("LanguageSwitcher", () => {
  beforeEach(() => {
    refresh.mockClear();
    // biome-ignore lint/suspicious/noDocumentCookie: reset test state.
    document.cookie = "aq_locale=; Max-Age=0; Path=/";
  });

  it("persists the choice in a cookie and refreshes the page", async () => {
    wrap("en", <LanguageSwitcher />);
    await userEvent.selectOptions(screen.getByLabelText("Language"), "or");
    expect(document.cookie).toContain("aq_locale=or");
    expect(refresh).toHaveBeenCalled();
  });

  it("offers each language under its own name", () => {
    wrap("en", <LanguageSwitcher />);
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual([
      "English",
      "हिन्दी",
      "ଓଡ଼ିଆ",
    ]);
  });
});
