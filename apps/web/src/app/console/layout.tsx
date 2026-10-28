import type { Metadata } from "next";
import type { ReactNode } from "react";

export const metadata: Metadata = {
  title: { default: "Console", template: "%s · AquaCreds Console" },
};

export default function ConsoleRootLayout({ children }: { children: ReactNode }) {
  return <div className="surface-dark min-h-dvh bg-deep-ocean text-fg">{children}</div>;
}
