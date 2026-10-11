"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Alert, Button, cx } from "@/components/ui";
import { Badge, PageHeader, Skeleton } from "@/components/form";
import { isSetupError } from "@/lib/inbox-api";
import { insightsApi, timeAgo, type NotificationItem } from "@/lib/insights-api";
import { useLiveMarks } from "@/lib/live";
import { errorText } from "@/lib/settings-api";

const PAGE = 30;

const human = (s: string) => {
  const t = s.replace(/_/g, " ").trim();
  return t ? t[0].toUpperCase() + t.slice(1) : "Notification";
};

const PLATFORM = (p: string) => (p === "whatsapp" ? "WhatsApp" : p === "facebook" ? "Facebook" : p === "instagram" ? "Instagram" : human(p));

/** Where the notification points: comment notifications go to the comments page, everything else to the inbox. */
function destination(n: NotificationItem): string | null {
  if (n.source_type.includes("comment")) return n.platform === "facebook" || n.platform === "instagram" ? `/comments/${n.platform}` : null;
  return ["whatsapp", "facebook", "instagram"].includes(n.platform) ? `/inbox/${n.platform}` : null;
}

function who(n: NotificationItem): string {
  return n.customer_name || (n.customer_username ? `@${n.customer_username}` : "Someone");
}

function merge(current: NotificationItem[], fresh: NotificationItem[]): NotificationItem[] {
  const map = new Map(current.map((n) => [n.id, n]));
  for (const n of fresh) map.set(n.id, n);
  return [...map.values()].sort((a, b) => b.id - a.id);
}

export function NotificationsView() {
  const marks = useLiveMarks();
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [items, setItems] = useState<NotificationItem[] | null>(null);
  const [unread, setUnread] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [paged, setPaged] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchFirst = useCallback(() => insightsApi.notifications({ unread: unreadOnly, limit: PAGE }), [unreadOnly]);

  useEffect(() => {
    let cancelled = false;
    setItems(null);
    setLoadError(null);
    setPaged(false);
    fetchFirst()
      .then((r) => {
        if (cancelled) return;
        setItems(r.notifications);
        setUnread(r.unread);
        setHasMore(r.notifications.length >= PAGE);
      })
      .catch((e: unknown) => !cancelled && setLoadError(e));
    return () => {
      cancelled = true;
    };
  }, [fetchFirst]);

  const refresh = useCallback(async () => {
    try {
      const r = await fetchFirst();
      setItems((c) => (c && paged ? merge(c, r.notifications) : r.notifications));
      setUnread(r.unread);
      if (!paged) setHasMore(r.notifications.length >= PAGE);
    } catch {
      /* ignore: background refresh */
    }
  }, [fetchFirst, paged]);

  useEffect(() => {
    if (!marks) return;
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per live event
  }, [marks]);

  async function loadMore() {
    const last = items?.[items.length - 1]?.id;
    if (!last) return;
    setLoadingMore(true);
    try {
      const r = await insightsApi.notifications({ unread: unreadOnly, beforeId: last, limit: PAGE });
      setPaged(true);
      setItems((c) => merge(c ?? [], r.notifications));
      setHasMore(r.notifications.length >= PAGE);
      setUnread(r.unread);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setLoadingMore(false);
    }
  }

  async function markRead(ids?: number[]) {
    setBusy(true);
    setError(null);
    try {
      await insightsApi.markRead(ids);
      const now = new Set(ids);
      setItems((c) => {
        if (!c) return c;
        const updated = c.map((n) => (!ids || now.has(n.id) ? { ...n, is_read: true } : n));
        return unreadOnly ? updated.filter((n) => !n.is_read) : updated;
      });
      setUnread((u) => (ids ? Math.max(0, u - ids.length) : 0));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  if (isSetupError(loadError)) {
    return (
      <div className="mx-auto max-w-lg space-y-4 py-16 text-center">
        <h1 className="text-xl font-semibold">Connect your database first</h1>
        <p className="text-sm text-muted">Notifications are stored in your own database.</p>
        <Link href="/settings/database" className="inline-block rounded-lg bg-brand px-4 py-2.5 text-sm font-medium text-white hover:bg-brand-hover">
          Go to database setup
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="Notifications"
        description="Handoff requests, new customers and other events that need your attention."
        actions={
          <>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={unreadOnly} onChange={(e) => setUnreadOnly(e.target.checked)} className="h-4 w-4 accent-[var(--brand)]" />
              Unread only
            </label>
            <Button variant="secondary" className="px-3 py-1.5" onClick={() => void markRead()} loading={busy} disabled={unread === 0}>
              Mark all read{unread > 0 ? ` (${unread})` : ""}
            </Button>
          </>
        }
      />

      {loadError !== null && <Alert tone="error">{errorText(loadError)}</Alert>}
      {error && <Alert tone="error">{error}</Alert>}

      {items === null && loadError === null ? (
        <div className="space-y-2">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-16" />
          ))}
        </div>
      ) : items && items.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border px-4 py-14 text-center">
          <p className="font-medium">{unreadOnly ? "You're all caught up" : "No notifications yet"}</p>
          <p className="mt-1 text-sm text-muted">{unreadOnly ? "There is nothing unread." : "They appear here when the bot hands a conversation over or something needs you."}</p>
        </div>
      ) : (
        items && (
          <>
            <ul className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-surface shadow-sm">
              {items.map((n) => {
                const href = destination(n);
                return (
                  <li key={n.id} className={cx("flex flex-wrap items-start gap-3 px-4 py-3.5", !n.is_read && "bg-brand-soft/40")}>
                    <span className={cx("mt-1.5 h-2 w-2 shrink-0 rounded-full", n.is_read ? "bg-transparent" : "bg-brand")} aria-label={n.is_read ? undefined : "Unread"} />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-medium">{human(n.type)}</span>
                        <Badge tone="neutral">{PLATFORM(n.platform)}</Badge>
                        <span className="text-xs text-muted">{timeAgo(n.created_at)}</span>
                      </div>
                      <p className="mt-0.5 text-sm text-muted">
                        <span className="font-medium text-foreground">{who(n)}</span>
                        {n.message_preview ? `: ${n.message_preview}` : ""}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      {href && (
                        <Link href={href} onClick={() => !n.is_read && void markRead([n.id])} className="text-sm text-brand hover:underline">
                          Open
                        </Link>
                      )}
                      {!n.is_read && (
                        <Button variant="ghost" className="px-2.5 py-1 text-xs" onClick={() => void markRead([n.id])} disabled={busy}>
                          Mark read
                        </Button>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
            {hasMore && (
              <div className="text-center">
                <Button variant="secondary" onClick={() => void loadMore()} loading={loadingMore}>
                  Load more
                </Button>
              </div>
            )}
          </>
        )
      )}
    </div>
  );
}
