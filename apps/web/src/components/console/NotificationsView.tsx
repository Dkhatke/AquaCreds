"use client";

import type { Schema } from "@aquacreds/api-client";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { Button, Card, EmptyState, ErrorState, LoadingState, StatusPill } from "@/components/ui";
import { errorMessage } from "@/lib/api-browser";
import { formatDate } from "@/lib/format";
import {
  useMarkAllRead,
  useMarkRead,
  useNotificationPrefs,
  useNotifications,
  useSetPreference,
} from "@/lib/overview-queries";

const PAGE = 20;

/** Only same-origin paths from the API are linked. */
export function safeLink(link: string | null): string | null {
  return link?.startsWith("/") && !link.startsWith("//") ? link : null;
}

export function NotificationsView() {
  const t = useTranslations("notifications");
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [offset, setOffset] = useState(0);
  const list = useNotifications(unreadOnly, offset, PAGE);
  const markRead = useMarkRead();
  const markAll = useMarkAllRead();

  const pages = list.data ? Math.max(1, Math.ceil(list.data.total / PAGE)) : 1;
  const page = Math.floor(offset / PAGE) + 1;

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-4 md:p-8">
      <div>
        <h1 className="font-display text-3xl font-semibold">{t("title")}</h1>
        <p className="mt-2 max-w-2xl text-fg-muted">{t("intro")}</p>
      </div>

      <Card
        title={t("title")}
        meta={t("apiLanguage")}
        actions={
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={unreadOnly}
                onChange={(e) => {
                  setUnreadOnly(e.target.checked);
                  setOffset(0);
                }}
                className="size-4 accent-[var(--color-lagoon-mint)]"
              />
              {t("unreadOnly")}
            </label>
            <Button
              variant="secondary"
              size="sm"
              disabled={markAll.isPending || (list.data?.unread ?? 0) === 0}
              onClick={() => markAll.mutate()}
            >
              {t("markAllRead")}
            </Button>
          </div>
        }
      >
        {list.isPending ? (
          <LoadingState label={t("title")} />
        ) : list.isError ? (
          <ErrorState
            title={t("loadError")}
            error={list.error}
            onRetry={() => void list.refetch()}
          />
        ) : list.data.items.length === 0 ? (
          <EmptyState title={unreadOnly ? t("emptyUnread") : t("empty")}>
            {t("emptyBody")}
          </EmptyState>
        ) : (
          <>
            <ul className="divide-y divide-line">
              {list.data.items.map((n) => (
                <NotificationRow
                  key={n.id}
                  n={n}
                  busy={markRead.isPending && markRead.variables === n.id}
                  onRead={() => markRead.mutate(n.id)}
                />
              ))}
            </ul>
            {(markRead.isError || markAll.isError) && (
              <p role="alert" className="mt-3 text-sm text-fail">
                {errorMessage(markRead.error ?? markAll.error)}
              </p>
            )}
            <nav
              className="mt-4 flex items-center justify-between"
              aria-label={t("page", { page, pages })}
            >
              <Button
                variant="ghost"
                size="sm"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE))}
              >
                {t("previous")}
              </Button>
              <span className="tabular text-sm text-fg-muted">{t("page", { page, pages })}</span>
              <Button
                variant="ghost"
                size="sm"
                disabled={page >= pages}
                onClick={() => setOffset(offset + PAGE)}
              >
                {t("next")}
              </Button>
            </nav>
          </>
        )}
      </Card>

      <PreferencesCard />
    </div>
  );
}

function NotificationRow({
  n,
  busy,
  onRead,
}: {
  n: Schema<"NotificationOut">;
  busy: boolean;
  onRead: () => void;
}) {
  const t = useTranslations("notifications");
  const href = safeLink(n.link);
  const unread = n.read_at === null;
  return (
    <li className="flex flex-wrap items-start justify-between gap-3 py-3">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <StatusPill status={unread ? "review" : "neutral"}>
            {unread ? t("unread") : t("read")}
          </StatusPill>
          <span className="text-xs text-fg-muted">{t(`kinds.${n.kind}`)}</span>
          {n.severity && (
            <StatusPill status={n.severity === "high" ? "failed" : "neutral"}>
              {n.severity}
            </StatusPill>
          )}
        </div>
        <p className={unread ? "mt-1 text-sm font-semibold" : "mt-1 text-sm"}>{n.title}</p>
        <p className="mt-0.5 text-sm text-fg-muted">{n.body}</p>
        <p className="tabular mt-1 text-xs text-fg-muted">{formatDate(n.created_at)}</p>
      </div>
      <div className="flex items-center gap-2">
        {href && (
          <Link href={href} className="text-sm text-lagoon-mint underline-offset-2 hover:underline">
            {t("open")}
          </Link>
        )}
        {unread && (
          <Button variant="ghost" size="sm" disabled={busy} onClick={onRead}>
            {t("markRead")}
          </Button>
        )}
      </div>
    </li>
  );
}

function PreferencesCard() {
  const t = useTranslations("notifications");
  const prefs = useNotificationPrefs();
  const set = useSetPreference();
  return (
    <Card title={t("prefs.title")} meta={t("prefs.meta")}>
      {prefs.isPending ? (
        <LoadingState label={t("prefs.title")} />
      ) : prefs.isError ? (
        <ErrorState error={prefs.error} onRetry={() => void prefs.refetch()} />
      ) : (
        <>
          {!prefs.data.email_enabled && (
            <p className="mb-3 rounded-lg border border-review-amber/60 px-3 py-2 text-sm text-warn">
              {t("prefs.emailOff")}
            </p>
          )}
          <ul className="divide-y divide-line">
            {prefs.data.preferences.map((p) => (
              <li key={p.kind} className="flex flex-wrap items-center justify-between gap-3 py-2.5">
                <div className="min-w-0">
                  <p className="text-sm font-medium">{t(`kinds.${p.kind}`)}</p>
                  <p className="text-xs text-fg-muted">
                    {p.customised ? t("prefs.customised") : t("prefs.default")}
                    {p.note ? ` · ${p.note}` : ""}
                  </p>
                </div>
                <div className="flex items-center gap-3">
                  <label className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={p.email}
                      disabled={set.isPending}
                      onChange={(e) => set.mutate({ kind: p.kind, email: e.target.checked })}
                      className="size-4 accent-[var(--color-lagoon-mint)]"
                    />
                    {t("prefs.email")}
                  </label>
                  {p.customised && (
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={set.isPending}
                      onClick={() => set.mutate({ kind: p.kind, email: null })}
                    >
                      {t("prefs.reset")}
                    </Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
          {set.isError && (
            <p role="alert" className="mt-3 text-sm text-fail">
              {errorMessage(set.error)}
            </p>
          )}
        </>
      )}
    </Card>
  );
}
