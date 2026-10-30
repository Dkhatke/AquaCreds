"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/cn";

const LINKS = [
  { href: "/registry", label: "Projects", match: /^\/registry(\/projects.*)?$/ },
  { href: "/registry/vintages", label: "Vintages", match: /^\/registry\/vintages/ },
  { href: "/registry/retirements", label: "Retirements", match: /^\/registry\/retirements/ },
  { href: "/registry/verify", label: "Verify a report hash", match: /^\/registry\/verify/ },
  { href: "/marketplace", label: "Marketplace", match: /^\/marketplace/ },
];

export function RegistryNav() {
  const pathname = usePathname();
  return (
    <nav aria-label="Public registry" className="border-b border-line bg-panel">
      <ul className="mx-auto flex max-w-6xl gap-1 overflow-x-auto px-5 md:px-8">
        {LINKS.map((l) => {
          const active = l.match.test(pathname);
          return (
            <li key={l.href}>
              <Link
                href={l.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "inline-block whitespace-nowrap border-b-2 px-3 py-3 text-sm font-medium",
                  active
                    ? "border-mint-ink text-fg"
                    : "border-transparent text-fg-muted hover:text-fg",
                )}
              >
                {l.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
