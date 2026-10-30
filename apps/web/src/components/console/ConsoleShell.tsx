"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { Logo } from "@/components/brand/Logo";
import { HonestyNote } from "@/components/i18n/HonestyNote";
import { LanguageSwitcher } from "@/components/i18n/LanguageSwitcher";
import { TranslationNotice } from "@/components/i18n/TranslationNotice";
import { Button, DemoBanner } from "@/components/ui";
import { postBff } from "@/lib/api-browser";
import { cn } from "@/lib/cn";
import { SECTIONS } from "@/lib/console-nav";
import { NotificationBell } from "./NotificationBell";
import { ProjectSwitcher } from "./ProjectSwitcher";
import { Providers } from "./Providers";

const PROJECT_SCOPED = new Set(["monitoring", "reports", "verification"]);

/** Account-level pages, listed under the sections in the sidebar. */
const ACCOUNT_LINKS = [
  { key: "notifications", href: "/console/notifications" },
  { key: "account", href: "/console/account" },
] as const;

const PROJECT_PATH = /^\/console\/projects\/([^/]+)/;

export function currentProjectId(pathname: string): string | null {
  return PROJECT_PATH.exec(pathname)?.[1] ?? null;
}

function sectionHref(slug: string, href: string, projectId: string | null) {
  return PROJECT_SCOPED.has(slug) && projectId ? `/console/projects/${projectId}/${slug}` : href;
}

function isActive(slug: string, href: string, pathname: string) {
  if (slug === "overview") return pathname === "/console";
  if (PROJECT_SCOPED.has(slug)) {
    return pathname === href || new RegExp(`^/console/projects/[^/]+/${slug}$`).test(pathname);
  }
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function ConsoleShell({
  demo,
  banner,
  children,
}: {
  demo: boolean;
  banner: string | null;
  children: ReactNode;
}) {
  return (
    <Providers>
      <Shell demo={demo} banner={banner}>
        {children}
      </Shell>
    </Providers>
  );
}

function Shell({
  demo,
  banner,
  children,
}: {
  demo: boolean;
  banner: string | null;
  children: ReactNode;
}) {
  const t = useTranslations("console");
  const pathname = usePathname();
  const router = useRouter();
  const projectId = currentProjectId(pathname);
  const [navOpen, setNavOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);

  const signOut = async () => {
    setSigningOut(true);
    try {
      await postBff("/bff/auth/logout");
    } finally {
      router.replace(demo ? "/" : "/console/login");
      router.refresh();
    }
  };

  return (
    <div className="surface-dark flex min-h-dvh flex-col bg-deep-ocean text-fg">
      <a
        href="#console-main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-2 focus:z-50 focus:rounded focus:bg-lagoon-mint focus:px-3 focus:py-2 focus:text-deep-ocean"
      >
        {t("shell.skip")}
      </a>
      {demo && <DemoBanner text={banner} />}
      <div className="flex min-h-0 flex-1">
        <aside
          id="console-nav"
          className={cn(
            "w-64 shrink-0 flex-col border-r border-line bg-mangrove lg:flex",
            navOpen ? "fixed inset-y-0 left-0 z-40 flex" : "hidden",
          )}
        >
          <div className="flex h-14 items-center px-4">
            <Link href="/" aria-label={t("shell.home")}>
              <Logo />
            </Link>
          </div>
          <nav aria-label={t("nav.label")} className="flex-1 overflow-y-auto px-2 py-3">
            <ul className="space-y-0.5">
              {SECTIONS.map((s) => {
                const href = sectionHref(s.slug, s.href, projectId);
                const active = isActive(s.slug, href, pathname);
                return (
                  <li key={s.slug}>
                    <Link
                      href={href}
                      aria-current={active ? "page" : undefined}
                      onClick={() => setNavOpen(false)}
                      className={cn(
                        "flex items-center justify-between rounded-lg px-3 py-2 text-sm",
                        active
                          ? "bg-deep-ocean font-medium text-fg"
                          : "text-fg-muted hover:bg-panel-raised hover:text-fg",
                      )}
                    >
                      {t(`nav.${s.slug}`)}
                      {active && (
                        <span aria-hidden="true" className="size-1.5 rounded-full bg-lagoon-mint" />
                      )}
                    </Link>
                  </li>
                );
              })}
              {ACCOUNT_LINKS.map((l) => {
                const active = pathname === l.href;
                return (
                  <li key={l.key}>
                    <Link
                      href={l.href}
                      aria-current={active ? "page" : undefined}
                      onClick={() => setNavOpen(false)}
                      className={cn(
                        "flex items-center justify-between rounded-lg px-3 py-2 text-sm",
                        active
                          ? "bg-deep-ocean font-medium text-fg"
                          : "text-fg-muted hover:bg-panel-raised hover:text-fg",
                      )}
                    >
                      {t(`nav.${l.key}`)}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </nav>
          <div className="space-y-2 border-t border-line px-4 py-3 text-[11px] leading-snug text-fg-muted">
            <HonestyNote kind="carbon" />
            <TranslationNotice />
          </div>
        </aside>
        {navOpen && (
          <button
            type="button"
            aria-label={t("shell.closeNav")}
            className="fixed inset-0 z-30 bg-black/50 lg:hidden"
            onClick={() => setNavOpen(false)}
          />
        )}

        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex h-14 shrink-0 items-center gap-3 border-b border-line bg-deep-ocean px-3 md:px-5">
            <Button
              variant="ghost"
              size="sm"
              className="lg:hidden"
              aria-expanded={navOpen}
              aria-controls="console-nav"
              onClick={() => setNavOpen((o) => !o)}
            >
              {t("shell.menu")}
            </Button>
            <ProjectSwitcher currentId={projectId} />
            <div className="ml-auto flex items-center gap-2">
              <LanguageSwitcher />
              <NotificationBell />
              {demo && (
                <span className="hidden rounded border border-review-amber/60 px-2 py-0.5 text-xs font-medium text-warn sm:inline">
                  {t("shell.demoSession")}
                </span>
              )}
              <Button variant="ghost" size="sm" onClick={signOut} disabled={signingOut}>
                {demo ? t("shell.leaveDemo") : t("shell.signOut")}
              </Button>
            </div>
          </header>
          <main id="console-main" className="relative min-h-0 flex-1">
            {children}
          </main>
        </div>
      </div>
    </div>
  );
}
