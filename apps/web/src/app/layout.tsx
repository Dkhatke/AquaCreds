import type { Metadata, Viewport } from "next";
import { Inter, Space_Grotesk } from "next/font/google";
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

export const metadata: Metadata = {
  title: { default: "AquaCreds: blue carbon MRV", template: "%s · AquaCreds" },
  description:
    "AquaCreds combines AI-driven ecosystem analysis, satellite intelligence and blockchain-backed records to make blue carbon monitoring more transparent.",
};

export const viewport: Viewport = { themeColor: "#0B2024" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${display.variable} ${sans.variable}`}>
      <body className="min-h-dvh antialiased">{children}</body>
    </html>
  );
}
