"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { useUnreadCount } from "@/lib/overview-queries";

/** Header bell: unread count, polled with backoff (see `useUnreadCount`). */
export function NotificationBell() {
  const t = useTranslations("notifications");
  const q = useUnreadCount();
  const count = q.data ?? 0;
  return (
    <Link
      href="/console/notifications"
      aria-label={count > 0 ? t("bellUnread", { count }) : t("bell")}
      className="relative inline-flex size-8 items-center justify-center rounded-full text-fg hover:bg-panel-raised"
    >
      <svg
        aria-hidden="true"
        viewBox="0 0 24 24"
        className="size-5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M6 9a6 6 0 1 1 12 0c0 5 2 6.5 2 6.5H4S6 14 6 9Z" />
        <path d="M10 19a2 2 0 0 0 4 0" />
      </svg>
      {count > 0 && (
        <span className="tabular absolute -right-1 -top-1 min-w-[1.1rem] rounded-full bg-lagoon-mint px-1 text-center text-[11px] font-semibold leading-[1.1rem] text-deep-ocean">
          {count > 99 ? "99+" : count}
        </span>
      )}
    </Link>
  );
}
