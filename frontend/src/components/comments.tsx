"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Alert, Button, cx } from "@/components/ui";
import { Badge, Select, Skeleton, type BadgeTone } from "@/components/form";
import { CLASSIFICATIONS, COMMENT_CHANNEL, COMMENT_LABEL, commentsApi, type CommentItem, type CommentPlatform } from "@/lib/comments-api";
import { isSetupError } from "@/lib/inbox-api";
import { useLiveMarks } from "@/lib/live";
import { errorText } from "@/lib/settings-api";

const PAGE = 40;
const BULK_CHUNK = 100;

type StatusFilter = "all" | "visible" | "hidden";

const TONE: Record<string, BadgeTone> = {
  friendly: "success",
  neutral: "neutral",
  confused: "info",
  frustrated: "warning",
  sarcastic: "warning",
  angry: "danger",
  spam: "danger",
  abusive: "danger",
  off_topic: "neutral",
};

const human = (s: string) => s.replace(/_/g, " ");

function mergeComments(current: CommentItem[], fresh: CommentItem[]): CommentItem[] {
  const map = new Map(current.map((c) => [c.comment_id, c]));
  for (const c of fresh) map.set(c.comment_id, c);
  return [...map.values()].sort((a, b) => b.created_at.localeCompare(a.created_at));
}

export function CommentsView({ platform }: { platform: CommentPlatform }) {
  const label = COMMENT_LABEL[platform];
  const marks = useLiveMarks();

  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [classification, setClassification] = useState("");

  const [comments, setComments] = useState<CommentItem[] | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [paged, setPaged] = useState(false); // true once "Load more" was used: live refreshes then merge instead of replace
  const [loadingMore, setLoadingMore] = useState(false);
  const [listError, setListError] = useState<unknown>(null);

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<string | null>(null); // a comment id, or "bulk"
  const [replyFor, setReplyFor] = useState<string | null>(null);
  const [replyText, setReplyText] = useState("");
  const [replyMode, setReplyMode] = useState<"public" | "private">("public");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [failures, setFailures] = useState<Array<{ id: string; error: string }>>([]);

  useEffect(() => {
    const t = setTimeout(() => setQ(search.trim()), 300);
    return () => clearTimeout(t);
  }, [search]);

  const fetchList = useCallback(
    (before?: string) =>
      commentsApi.list(platform, {
        q,
        hidden: status === "all" ? undefined : status === "hidden",
        unread: unreadOnly,
        classification,
        before,
        limit: PAGE,
      }),
    [platform, q, status, unreadOnly, classification],
  );

  useEffect(() => {
    let cancelled = false;
    setComments(null);
    setListError(null);
    setPaged(false);
    setSelected(new Set());
    setReplyFor(null);
    fetchList()
      .then((r) => {
        if (cancelled) return;
        setComments(r.comments);
        setHasMore(r.comments.length >= PAGE);
      })
      .catch((e: unknown) => !cancelled && setListError(e));
    return () => {
      cancelled = true;
    };
  }, [fetchList]);

  useEffect(() => {
    setStatus("all");
    setUnreadOnly(false);
    setClassification("");
    setSearch("");
    setError(null);
    setNotice(null);
    setFailures([]);
  }, [platform]);

  /** Background refresh: replaces the first page (so remote deletions disappear) unless older pages are loaded. */
  const refresh = useCallback(async () => {
    try {
      const r = await fetchList();
      setComments((c) => (c && paged ? mergeComments(c, r.comments) : r.comments));
      if (!paged) setHasMore(r.comments.length >= PAGE);
    } catch {
      /* ignore: background refresh */
    }
  }, [fetchList, paged]);

  useEffect(() => {
    if (!marks) return;
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per live event
  }, [marks]);

  async function loadMore() {
    const last = comments?.[comments.length - 1]?.created_at;
    if (!last) return;
    setLoadingMore(true);
    try {
      const r = await fetchList(last);
      setPaged(true);
      setComments((c) => mergeComments(c ?? [], r.comments));
      setHasMore(r.comments.length >= PAGE);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setLoadingMore(false);
    }
  }

  const patch = (id: string, change: Partial<CommentItem>) => setComments((c) => c?.map((x) => (x.comment_id === id ? { ...x, ...change } : x)) ?? c);
  const drop = (ids: string[]) => {
    const gone = new Set(ids);
    setComments((c) => c?.filter((x) => !gone.has(x.comment_id)) ?? c);
    setSelected((s) => new Set([...s].filter((id) => !gone.has(id))));
  };

  async function run(id: string, fn: () => Promise<void>) {
    setBusy(id);
    setError(null);
    setNotice(null);
    try {
      await fn();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  const sendReply = (c: CommentItem) =>
    run(c.comment_id, async () => {
      await commentsApi.reply(platform, c.comment_id, replyText.trim(), replyMode);
      setReplyFor(null);
      setReplyText("");
      setNotice(replyMode === "private" ? "Private reply sent." : "Reply posted.");
      patch(c.comment_id, { is_read: true });
      await refresh();
    });

  const toggleHide = (c: CommentItem) =>
    run(c.comment_id, async () => {
      await commentsApi.hide(platform, c.comment_id, !c.hidden);
      patch(c.comment_id, { hidden: !c.hidden });
    });

  const toggleBot = (c: CommentItem) =>
    run(c.comment_id, async () => {
      await commentsApi.setBot(platform, c.comment_id, !c.bot_active);
      patch(c.comment_id, { bot_active: !c.bot_active });
    });

  const markRead = (ids: string[]) =>
    run("bulk", async () => {
      await commentsApi.markRead(platform, ids);
      setComments((c) => c?.map((x) => (ids.includes(x.comment_id) ? { ...x, is_read: true } : x)) ?? c);
    });

  const removeOne = (c: CommentItem) => {
    if (!window.confirm(`Delete this comment from ${label}? It is removed from the platform first, and from your dashboard only once ${label} confirms.`)) return;
    return run(c.comment_id, async () => {
      await commentsApi.remove(platform, c.comment_id);
      drop([c.comment_id]);
      setNotice("Comment deleted.");
    });
  };

  async function bulkDelete() {
    const ids = [...selected];
    if (ids.length === 0) return;
    if (!window.confirm(`Delete ${ids.length} comment${ids.length === 1 ? "" : "s"} from ${label}? This cannot be undone.`)) return;
    setBusy("bulk");
    setError(null);
    setNotice(null);
    setFailures([]);
    let deleted = 0;
    const failed: Array<{ id: string; error: string }> = [];
    try {
      for (let i = 0; i < ids.length; i += BULK_CHUNK) {
        const r = await commentsApi.bulkDelete(platform, ids.slice(i, i + BULK_CHUNK));
        deleted += r.deleted;
        drop(r.results.filter((x) => x.ok).map((x) => x.id));
        for (const x of r.results) if (!x.ok) failed.push({ id: x.id, error: x.error ?? "Could not delete." });
      }
      setNotice(`Deleted ${deleted} of ${ids.length}.`);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setFailures(failed);
      setBusy(null);
    }
  }

  function toggleSelect(id: string) {
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  if (isSetupError(listError)) {
    return (
      <div className="mx-auto max-w-lg space-y-4 py-16 text-center">
        <h1 className="text-xl font-semibold">Connect your database first</h1>
        <p className="text-sm text-muted">Your {label} comments are stored in your own database. Connect it and run the setup to start.</p>
        <Link href="/settings/database" className="inline-block rounded-lg bg-brand px-4 py-2.5 text-sm font-medium text-white hover:bg-brand-hover">
          Go to database setup
        </Link>
      </div>
    );
  }

  const shown = comments ?? [];
  const allSelected = shown.length > 0 && shown.every((c) => selected.has(c.comment_id));
  const unreadShown = shown.filter((c) => !c.is_read).map((c) => c.comment_id);
  const filtered = status !== "all" || unreadOnly || classification !== "" || q !== "";

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">{label} comments</h1>
        <Link href={`/settings/channels/${COMMENT_CHANNEL[platform]}`} className="text-sm text-brand hover:underline">
          Channel settings
        </Link>
      </div>

      <div className="grid gap-3 rounded-xl border border-border bg-surface p-4 shadow-sm sm:grid-cols-2 lg:grid-cols-4">
        <label className="block space-y-1.5 sm:col-span-2 lg:col-span-1">
          <span className="block text-sm font-medium">Search</span>
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Comment text or author"
            className="block w-full rounded-lg border border-border bg-surface px-3 py-2.5 text-sm focus:border-brand focus:outline-none focus:ring-4 focus:ring-[var(--ring)]"
          />
        </label>
        <Select
          label="Visibility"
          value={status}
          onChange={(e) => setStatus(e.target.value as StatusFilter)}
          options={[
            { value: "all", label: "All" },
            { value: "visible", label: "Visible" },
            { value: "hidden", label: "Hidden" },
          ]}
        />
        <Select
          label="Tone"
          value={classification}
          onChange={(e) => setClassification(e.target.value)}
          options={[{ value: "", label: "Any" }, ...CLASSIFICATIONS.map((c) => ({ value: c, label: human(c)[0].toUpperCase() + human(c).slice(1) }))]}
        />
        <label className="flex items-end gap-2 pb-2.5 text-sm">
          <input type="checkbox" checked={unreadOnly} onChange={(e) => setUnreadOnly(e.target.checked)} className="h-4 w-4 accent-[var(--brand)]" />
          Unread only
        </label>
      </div>

      {error && <Alert tone="error">{error}</Alert>}
      {notice && <Alert tone="success">{notice}</Alert>}
      {failures.length > 0 && (
        <Alert tone="warning">
          <p className="font-medium">{failures.length} comment{failures.length === 1 ? "" : "s"} could not be deleted and are still on {label}:</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs">
            {[...new Map(failures.map((f) => [f.error, f])).values()].map((f) => (
              <li key={f.error}>
                {f.error} ({failures.filter((x) => x.error === f.error).length})
              </li>
            ))}
          </ul>
        </Alert>
      )}

      {comments && comments.length > 0 && (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-surface px-4 py-2.5 text-sm">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={allSelected}
              onChange={() => setSelected(allSelected ? new Set() : new Set(shown.map((c) => c.comment_id)))}
              className="h-4 w-4 accent-[var(--brand)]"
            />
            {selected.size > 0 ? `${selected.size} selected` : "Select all shown"}
          </label>
          <div className="ml-auto flex flex-wrap gap-2">
            {selected.size > 0 && (
              <>
                <Button variant="secondary" className="px-3 py-1.5" onClick={() => void markRead([...selected])} disabled={busy !== null}>
                  Mark read
                </Button>
                <Button variant="danger" className="px-3 py-1.5" onClick={() => void bulkDelete()} loading={busy === "bulk"} disabled={busy !== null}>
                  Delete selected
                </Button>
              </>
            )}
            {selected.size === 0 && unreadShown.length > 0 && (
              <Button variant="ghost" className="px-3 py-1.5" onClick={() => void markRead(unreadShown)} disabled={busy !== null}>
                Mark {unreadShown.length} shown as read
              </Button>
            )}
          </div>
        </div>
      )}

      {listError !== null && (
        <div className="space-y-3">
          <Alert tone="error">{errorText(listError)}</Alert>
        </div>
      )}

      {comments === null && listError === null && (
        <div className="space-y-3" role="status" aria-label="Loading comments">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className="h-28" />
          ))}
        </div>
      )}

      {comments?.length === 0 && (
        <div className="rounded-xl border border-dashed border-border bg-surface p-10 text-center text-sm text-muted">
          <p className="font-medium text-foreground">{filtered ? "No comments match these filters" : "No comments yet"}</p>
          <p className="mt-1">
            {filtered ? (
              "Try clearing a filter."
            ) : (
              <>
                New comments appear here once the webhook is connected in{" "}
                <Link href={`/settings/channels/${COMMENT_CHANNEL[platform]}`} className="text-brand hover:underline">
                  channel settings
                </Link>
                .
              </>
            )}
          </p>
        </div>
      )}

      <ul className="space-y-3">
        {shown.map((c) => {
          const author = c.from_name || (c.from_username ? `@${c.from_username}` : "Unknown");
          const working = busy === c.comment_id;
          return (
            <li key={c.comment_id} className={cx("rounded-xl border bg-surface p-4 shadow-sm", c.is_read ? "border-border" : "border-brand/40", c.hidden && "opacity-80")}>
              <div className="flex items-start gap-3">
                <input
                  type="checkbox"
                  checked={selected.has(c.comment_id)}
                  onChange={() => toggleSelect(c.comment_id)}
                  aria-label={`Select comment by ${author}`}
                  className="mt-1 h-4 w-4 accent-[var(--brand)]"
                />
                <div className="min-w-0 flex-1 space-y-2">
                  <div className="flex flex-wrap items-center gap-2">
                    {!c.is_read && <span className="h-2 w-2 rounded-full bg-brand" title="Unread" />}
                    <span className="font-medium">{author}</span>
                    <span className="text-xs text-muted">{new Date(c.created_at).toLocaleString()}</span>
                    {c.parent_id && <Badge tone="neutral">Reply in thread</Badge>}
                    {c.hidden && <Badge tone="warning">Hidden</Badge>}
                    {c.classification && <Badge tone={TONE[c.classification] ?? "neutral"}>{human(c.classification)}</Badge>}
                    {c.topic && <span className="text-xs text-muted">Topic: {c.topic}</span>}
                  </div>
                  <p className="whitespace-pre-wrap break-words text-sm leading-relaxed">{c.message || <em className="text-muted">(no text)</em>}</p>

                  {c.bot_reply && (
                    <div className="rounded-lg bg-surface-2 px-3 py-2 text-sm">
                      <p className="text-xs font-medium text-muted">
                        {c.action?.startsWith("agent") ? "Your reply" : "Bot reply"}
                        {c.action ? ` · ${human(c.action)}` : ""}
                      </p>
                      <p className="mt-0.5 whitespace-pre-wrap break-words">{c.bot_reply}</p>
                    </div>
                  )}
                  {!c.bot_reply && c.action && <p className="text-xs text-muted">Action: {human(c.action)}</p>}

                  {replyFor === c.comment_id ? (
                    <div className="space-y-2 rounded-lg border border-border p-3">
                      <div className="flex flex-wrap gap-4 text-sm" role="radiogroup" aria-label="Reply type">
                        {(["public", "private"] as const).map((m) => (
                          <label key={m} className="flex items-center gap-2">
                            <input type="radio" name={`mode-${c.comment_id}`} checked={replyMode === m} onChange={() => setReplyMode(m)} className="accent-[var(--brand)]" />
                            {m === "public" ? "Public reply" : "Private message"}
                          </label>
                        ))}
                      </div>
                      <textarea
                        value={replyText}
                        onChange={(e) => setReplyText(e.target.value)}
                        rows={3}
                        maxLength={2000}
                        autoFocus
                        aria-label="Reply text"
                        placeholder={replyMode === "public" ? "Visible to everyone under the post" : "Sent to the commenter's inbox. Only possible once per comment, shortly after it was posted."}
                        className="block w-full rounded-lg border border-border bg-surface px-3 py-2.5 text-sm focus:border-brand focus:outline-none focus:ring-4 focus:ring-[var(--ring)]"
                      />
                      <div className="flex gap-2">
                        <Button className="px-3 py-1.5" onClick={() => void sendReply(c)} loading={working} disabled={!replyText.trim() || busy !== null}>
                          Send {replyMode} reply
                        </Button>
                        <Button variant="ghost" className="px-3 py-1.5" onClick={() => setReplyFor(null)} disabled={working}>
                          Cancel
                        </Button>
                      </div>
                    </div>
                  ) : (
                    <div className="flex flex-wrap gap-2 pt-1">
                      <Button
                        variant="secondary"
                        className="px-3 py-1.5"
                        onClick={() => {
                          setReplyFor(c.comment_id);
                          setReplyText("");
                          setReplyMode("public");
                        }}
                        disabled={busy !== null}
                      >
                        Reply
                      </Button>
                      <Button variant="secondary" className="px-3 py-1.5" onClick={() => void toggleHide(c)} loading={working} disabled={busy !== null}>
                        {c.hidden ? "Unhide" : "Hide"}
                      </Button>
                      <Button variant="ghost" className="px-3 py-1.5" onClick={() => void toggleBot(c)} disabled={busy !== null}>
                        Bot: {c.bot_active ? "on" : "off"}
                      </Button>
                      {!c.is_read && (
                        <Button variant="ghost" className="px-3 py-1.5" onClick={() => void markRead([c.comment_id])} disabled={busy !== null}>
                          Mark read
                        </Button>
                      )}
                      <Button variant="ghost" className="ml-auto px-3 py-1.5 text-danger" onClick={() => void removeOne(c)} disabled={busy !== null}>
                        Delete
                      </Button>
                    </div>
                  )}
                </div>
              </div>
            </li>
          );
        })}
      </ul>

      {hasMore && (
        <div className="text-center">
          <Button variant="secondary" onClick={loadMore} loading={loadingMore}>
            Load more
          </Button>
        </div>
      )}
    </div>
  );
}
