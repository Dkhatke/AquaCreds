import type { Metadata, Viewport } from "next";
import { Inter, Noto_Sans_Devanagari, Noto_Sans_Oriya, Space_Grotesk } from "next/font/google";
import { NextIntlClientProvider } from "next-intl";
import { getLocale } from "next-intl/server";
import type { ReactNode } from "react";
import "./globals.css";

// next/font downloads the files at build time and serves them from this origin:
// no runtime requests to Google.
const display = Space_Grotesk({
  subsets: ["latin"],
  variable: "--font-space-grotesk",
  display: "swap",
});
const sans = Inter({ subsets: ["latin"], variable: "--font-inter", display: "swap" });
// Hindi and Odia glyphs (Inter has none). Self-hosted by next/font like the others; only fetched
// by the browser when a page actually contains Devanagari / Odia text (unicode-range subsets).
const devanagari = Noto_Sans_Devanagari({
  subsets: ["devanagari"],
  variable: "--font-devanagari",
  display: "swap",
});
const oriya = Noto_Sans_Oriya({ subsets: ["oriya"], variable: "--font-oriya", display: "swap" });

export const metadata: Metadata = {
  title: { default: "AquaCreds: blue carbon MRV", template: "%s · AquaCreds" },
  description:
    "AquaCreds combines AI-driven ecosystem analysis, satellite intelligence and blockchain-backed records to make blue carbon monitoring more transparent.",
};

export const viewport: Viewport = { themeColor: "#0B2024" };

export default async function RootLayout({ children }: { children: ReactNode }) {
  const locale = await getLocale();
  return (
    <html
      lang={locale}
      className={`${display.variable} ${sans.variable} ${devanagari.variable} ${oriya.variable}`}
    >
      <body className="min-h-dvh antialiased">
        <NextIntlClientProvider>{children}</NextIntlClientProvider>
      </body>
    </html>
  );
}
