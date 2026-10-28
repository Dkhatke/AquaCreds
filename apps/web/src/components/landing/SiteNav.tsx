import Link from "next/link";
import { Logo } from "@/components/brand/Logo";
import { ButtonLink } from "@/components/ui";
import { cn } from "@/lib/cn";

const LINKS = [
  { href: "/#platform", label: "Platform" },
  { href: "/#technology", label: "Technology" },
  { href: "/#impact", label: "Impact" },
  { href: "/marketplace", label: "Marketplace" },
];

/** Public site navigation. `overlay` = transparent over the hero imagery. */
export function SiteNav({ overlay = false }: { overlay?: boolean }) {
  return (
    <header
      className={cn(
        "z-20 w-full",
        overlay
          ? "absolute inset-x-0 top-0 surface-dark bg-transparent"
          : "surface-dark bg-deep-ocean",
      )}
    >
      <nav
        aria-label="Main"
        className="mx-auto flex max-w-7xl items-center gap-6 px-5 py-4 md:px-8"
      >
        <Link href="/" aria-label="AquaCreds home" className="text-fg">
          <Logo />
        </Link>
        <ul className="ml-auto hidden items-center gap-6 text-sm md:flex">
          {LINKS.map((l) => (
            <li key={l.href}>
              <Link href={l.href} className="text-fg/90 hover:text-lagoon-mint">
                {l.label}
              </Link>
            </li>
          ))}
        </ul>
        <ButtonLink href="/console/demo" size="sm" className="ml-auto md:ml-0">
          Explore Demo <span aria-hidden="true">↗</span>
        </ButtonLink>
        <details className="relative md:hidden">
          <summary className="cursor-pointer list-none rounded px-2 py-1 text-sm text-fg">
            Menu
          </summary>
          <ul className="absolute right-0 mt-2 w-44 rounded-lg border border-line bg-panel p-2 text-sm shadow-lg">
            {LINKS.map((l) => (
              <li key={l.href}>
                <Link href={l.href} className="block rounded px-2 py-2 hover:bg-panel-raised">
                  {l.label}
                </Link>
              </li>
            ))}
          </ul>
        </details>
      </nav>
    </header>
  );
}
