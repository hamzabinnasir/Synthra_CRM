"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Alert, Button, Spinner, cx } from "@/components/ui";
import { Badge, Skeleton } from "@/components/form";
import { errorText } from "@/lib/settings-api";
import {
  fetchMediaBlob,
  inboxApi,
  isSetupError,
  PLATFORM_CHANNEL,
  PLATFORM_LABEL,
  type ChatMessage,
  type Conversation,
  type InboxPlatform,
} from "@/lib/inbox-api";
import { useLiveMarks } from "@/lib/live";

const PAGE = 40;
const MAX_IMAGE_BYTES = 5_000_000;

type Filter = "all" | "unread" | "handoff";

/* ---------------------------------- helpers ---------------------------------- */

function listTime(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const now = new Date();
  if (d.toDateString() === now.toDateString()) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (d.toDateString() === yesterday.toDateString()) return "Yesterday";
  return d.toLocaleDateString([], d.getFullYear() === now.getFullYear() ? { day: "numeric", month: "short" } : { year: "numeric", month: "short", day: "numeric" });
}

function dayLabel(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  if (d.toDateString() === now.toDateString()) return "Today";
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (d.toDateString() === yesterday.toDateString()) return "Yesterday";
  return d.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long", year: "numeric" });
}

const initials = (name: string) =>
  name
    .replace(/^[^\p{L}\p{N}]+/u, "")
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .join("") || "?";

function mergeConversations(current: Conversation[], fresh: Conversation[]): Conversation[] {
  const map = new Map(current.map((c) => [c.psid, c]));
  for (const c of fresh) map.set(c.psid, c);
  return [...map.values()].sort((a, b) => (b.last_activity_at ?? "").localeCompare(a.last_activity_at ?? ""));
}

function mergeMessages(current: ChatMessage[], fresh: ChatMessage[]): ChatMessage[] {
  const map = new Map(current.map((m) => [m.id, m]));
  for (const m of fresh) map.set(m.id, m);
  return [...map.values()].sort((a, b) => a.id - b.id);
}

/* ------------------------------------ media ----------------------------------- */

function MediaMessage({ message }: { message: ChatMessage }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const source = message.media_url;
  const mediaId = source?.startsWith("media:") ? source.slice(6) : null;

  useEffect(() => {
    if (!mediaId) return;
    const controller = new AbortController();
    let objectUrl: string | null = null;
    fetchMediaBlob(mediaId, controller.signal)
      .then((blob) => {
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch((e: unknown) => {
        if ((e as Error).name !== "AbortError") setFailed(true);
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [mediaId]);

  const label = message.media_filename || message.media_type || "attachment";
  if (!mediaId || failed) return <p className="text-xs italic opacity-80">{label} (not available)</p>;
  if (!url) return <Skeleton className="h-32 w-48" />;
  if (message.media_type === "image" || message.media_mime_type?.startsWith("image/")) {
    return (
      <a href={url} target="_blank" rel="noreferrer">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={url} alt={label} className="max-h-72 max-w-full rounded-lg object-contain" />
      </a>
    );
  }
  return (
    <a href={url} download={label} className="underline">
      Download {label}
    </a>
  );
}

/* --------------------------------- conversation list --------------------------------- */

function Avatar({ name, size = "h-10 w-10" }: { name: string; size?: string }) {
  return (
    <span className={cx("flex shrink-0 items-center justify-center rounded-full bg-brand-soft text-sm font-semibold text-brand", size)} aria-hidden="true">
      {initials(name)}
    </span>
  );
}

/* ------------------------------------- main view ------------------------------------ */

export function InboxView({ platform }: { platform: InboxPlatform }) {
  const label = PLATFORM_LABEL[platform];
  const marks = useLiveMarks();

  const [filter, setFilter] = useState<Filter>("all");
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [conversations, setConversations] = useState<Conversation[] | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [listError, setListError] = useState<unknown>(null);

  const [selected, setSelected] = useState<string | null>(null);
  const [customer, setCustomer] = useState<Conversation | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [hasOlder, setHasOlder] = useState(false);
  const [chatLoading, setChatLoading] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);

  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const bottomRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const lastId = messages.length ? messages[messages.length - 1].id : 0;

  /* ---- conversation list ---- */
  useEffect(() => {
    const t = setTimeout(() => setQ(search.trim()), 300);
    return () => clearTimeout(t);
  }, [search]);

  const fetchList = useCallback(
    (before?: string) => inboxApi.conversations(platform, { q, unread: filter === "unread", handoff: filter === "handoff", before, limit: PAGE }),
    [platform, q, filter],
  );

  useEffect(() => {
    let cancelled = false;
    setConversations(null);
    setListError(null);
    fetchList()
      .then((r) => {
        if (cancelled) return;
        setConversations(r.conversations);
        setHasMore(r.conversations.length >= PAGE);
      })
      .catch((e: unknown) => !cancelled && setListError(e));
    return () => {
      cancelled = true;
    };
  }, [fetchList]);

  async function loadMore() {
    const last = conversations?.[conversations.length - 1]?.last_activity_at;
    if (!last) return;
    setLoadingMore(true);
    try {
      const r = await fetchList(last);
      setConversations((c) => mergeConversations(c ?? [], r.conversations));
      setHasMore(r.conversations.length >= PAGE);
    } catch (e) {
      setActionError(errorText(e));
    } finally {
      setLoadingMore(false);
    }
  }

  const refreshList = useCallback(async () => {
    try {
      const r = await fetchList();
      setConversations((c) => (c ? mergeConversations(c, r.conversations) : r.conversations));
    } catch {
      /* a failed background refresh is not worth interrupting the user */
    }
  }, [fetchList]);

  /* ---- open conversation ---- */
  const refreshChat = useCallback(async () => {
    if (!selected) return;
    try {
      const r = await inboxApi.messages(platform, selected);
      setCustomer(r.customer);
      setMessages((m) => mergeMessages(m, r.messages));
      if (r.customer.unread_count > 0) {
        await inboxApi.markRead(platform, selected);
        setConversations((list) => list?.map((c) => (c.psid === selected ? { ...c, unread_count: 0 } : c)) ?? list);
      }
    } catch (e) {
      setChatError(errorText(e));
    }
  }, [platform, selected]);

  useEffect(() => {
    setMessages([]);
    setCustomer(null);
    setChatError(null);
    setActionError(null);
    setDraft("");
    setHasOlder(false);
    if (!selected) return;
    let cancelled = false;
    setChatLoading(true);
    inboxApi
      .messages(platform, selected)
      .then(async (r) => {
        if (cancelled) return;
        setCustomer(r.customer);
        setMessages(r.messages);
        setHasOlder(r.messages.length >= 100);
        if (r.customer.unread_count > 0) {
          await inboxApi.markRead(platform, selected).catch(() => undefined);
          setConversations((list) => list?.map((c) => (c.psid === selected ? { ...c, unread_count: 0 } : c)) ?? list);
        }
      })
      .catch((e: unknown) => !cancelled && setChatError(errorText(e)))
      .finally(() => !cancelled && setChatLoading(false));
    return () => {
      cancelled = true;
    };
  }, [platform, selected]);

  useEffect(() => {
    setSelected(null);
    setFilter("all");
    setSearch("");
  }, [platform]);

  /* live updates: the server only says "something changed", so refetch what is on screen */
  useEffect(() => {
    if (!marks) return;
    void refreshList();
    void refreshChat();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run once per live event, not whenever a callback identity changes
  }, [marks]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [lastId, selected]);

  async function loadOlder() {
    if (!selected || messages.length === 0) return;
    try {
      const r = await inboxApi.messages(platform, selected, messages[0].id);
      setMessages((m) => mergeMessages(m, r.messages));
      setHasOlder(r.messages.length >= 100);
    } catch (e) {
      setActionError(errorText(e));
    }
  }

  /* ---- actions ---- */
  async function act(fn: () => Promise<unknown>) {
    setActionError(null);
    try {
      await fn();
      await Promise.all([refreshChat(), refreshList()]);
    } catch (e) {
      setActionError(errorText(e));
    }
  }

  async function sendText() {
    const text = draft.trim();
    if (!text || !selected || sending) return;
    setSending(true);
    setActionError(null);
    try {
      await inboxApi.send(platform, selected, text);
      setDraft("");
      await Promise.all([refreshChat(), refreshList()]);
    } catch (e) {
      setActionError(errorText(e));
    } finally {
      setSending(false);
    }
  }

  async function sendImage(file: File | undefined) {
    if (!file || !selected) return;
    if (!["image/jpeg", "image/png"].includes(file.type)) return setActionError("Only JPEG or PNG images can be sent.");
    if (file.size > MAX_IMAGE_BYTES) return setActionError("That image is larger than 5 MB.");
    setSending(true);
    setActionError(null);
    try {
      await inboxApi.sendImage(platform, selected, file);
      await Promise.all([refreshChat(), refreshList()]);
    } catch (e) {
      setActionError(errorText(e));
    } finally {
      setSending(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  const onComposerKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void sendText();
    }
  };

  const grouped = useMemo(() => {
    const out: Array<{ day: string; items: ChatMessage[] }> = [];
    for (const m of messages) {
      const day = dayLabel(m.created_at);
      const last = out[out.length - 1];
      if (last && last.day === day) last.items.push(m);
      else out.push({ day, items: [m] });
    }
    return out;
  }, [messages]);

  /* ---- render ---- */
  if (isSetupError(listError)) {
    return (
      <div className="mx-auto max-w-lg space-y-4 py-16 text-center">
        <h1 className="text-xl font-semibold">Connect your database first</h1>
        <p className="text-sm text-muted">Your {label} conversations are stored in your own database. Connect it and run the setup to start.</p>
        <Link href="/settings/database" className="inline-block rounded-lg bg-brand px-4 py-2.5 text-sm font-medium text-white hover:bg-brand-hover">
          Go to database setup
        </Link>
      </div>
    );
  }

  const name = customer?.display_name ?? conversations?.find((c) => c.psid === selected)?.display_name ?? "";

  return (
    <div className="flex h-[calc(100dvh-9rem)] min-h-[32rem] flex-col gap-3">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">{label} inbox</h1>
        <Link href={`/settings/channels/${PLATFORM_CHANNEL[platform]}`} className="text-sm text-brand hover:underline">
          Channel settings
        </Link>
      </div>

      <div className="grid min-h-0 flex-1 overflow-hidden rounded-xl border border-border bg-surface shadow-sm md:grid-cols-[22rem_1fr]">
        {/* list */}
        <aside className={cx("flex min-h-0 flex-col border-border md:border-r", selected && "hidden md:flex")}>
          <div className="space-y-3 border-b border-border p-3">
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search name, number or message"
              aria-label="Search conversations"
              className="block w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm focus:border-brand focus:outline-none focus:ring-4 focus:ring-[var(--ring)]"
            />
            <div className="flex gap-1" role="tablist" aria-label="Filter conversations">
              {(["all", "unread", "handoff"] as const).map((f) => (
                <button
                  key={f}
                  role="tab"
                  aria-selected={filter === f}
                  onClick={() => setFilter(f)}
                  className={cx("rounded-full px-3 py-1 text-xs font-medium transition", filter === f ? "bg-brand text-white" : "bg-surface-2 text-muted hover:text-foreground")}
                >
                  {f === "all" ? "All" : f === "unread" ? "Unread" : "Needs a person"}
                </button>
              ))}
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto">
            {listError !== null && (
              <div className="space-y-2 p-3">
                <Alert tone="error">{errorText(listError)}</Alert>
              </div>
            )}
            {conversations === null && listError === null && (
              <div className="space-y-3 p-3" role="status" aria-label="Loading conversations">
                {Array.from({ length: 6 }, (_, i) => (
                  <Skeleton key={i} className="h-14" />
                ))}
              </div>
            )}
            {conversations?.length === 0 && (
              <div className="p-6 text-center text-sm text-muted">
                <p className="font-medium text-foreground">{filter === "all" && !q ? "No conversations yet" : "Nothing matches"}</p>
                <p className="mt-1">
                  {filter === "all" && !q ? (
                    <>
                      Messages appear here once customers write to you. Make sure the webhook is set up in{" "}
                      <Link href={`/settings/channels/${PLATFORM_CHANNEL[platform]}`} className="text-brand hover:underline">
                        channel settings
                      </Link>
                      .
                    </>
                  ) : (
                    "Try a different search or filter."
                  )}
                </p>
              </div>
            )}
            <ul>
              {conversations?.map((c) => (
                <li key={c.psid}>
                  <button
                    onClick={() => setSelected(c.psid)}
                    aria-current={selected === c.psid}
                    className={cx("flex w-full items-center gap-3 border-b border-border/60 px-3 py-3 text-left transition hover:bg-surface-2", selected === c.psid && "bg-brand-soft")}
                  >
                    <Avatar name={c.display_name} />
                    <span className="min-w-0 flex-1">
                      <span className="flex items-baseline justify-between gap-2">
                        <span className={cx("truncate text-sm", c.unread_count > 0 ? "font-semibold" : "font-medium")}>{c.display_name}</span>
                        <span className="shrink-0 text-[11px] text-muted">{listTime(c.last_activity_at)}</span>
                      </span>
                      <span className="mt-0.5 flex items-center justify-between gap-2">
                        <span className="truncate text-xs text-muted">{c.last_message_text || "Attachment"}</span>
                        <span className="flex shrink-0 items-center gap-1.5">
                          {c.handoff_requested && <span className="rounded bg-warning-soft px-1.5 py-0.5 text-[10px] font-medium text-warning">Needs you</span>}
                          {!c.bot_active && <span className="rounded bg-surface-2 px-1.5 py-0.5 text-[10px] font-medium text-muted">Bot off</span>}
                          {c.unread_count > 0 && (
                            <span className="min-w-5 rounded-full bg-brand px-1.5 py-0.5 text-center text-[11px] font-semibold text-white">{c.unread_count > 99 ? "99+" : c.unread_count}</span>
                          )}
                        </span>
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            {hasMore && (
              <div className="p-3">
                <Button variant="secondary" full onClick={loadMore} loading={loadingMore}>
                  Load more
                </Button>
              </div>
            )}
          </div>
        </aside>

        {/* chat */}
        <section className={cx("flex min-h-0 flex-col", !selected && "hidden md:flex")}>
          {!selected ? (
            <div className="flex flex-1 items-center justify-center p-8 text-center text-sm text-muted">Select a conversation to read and reply.</div>
          ) : (
            <>
              <header className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-3">
                <button onClick={() => setSelected(null)} className="rounded-lg p-1.5 text-muted hover:bg-surface-2 md:hidden" aria-label="Back to conversations">
                  ←
                </button>
                <Avatar name={name || "?"} size="h-9 w-9" />
                <div className="min-w-0 flex-1">
                  <h2 className="truncate font-semibold">{name || "Conversation"}</h2>
                  {customer?.handoff_requested && <Badge tone="warning">Waiting for a person</Badge>}
                </div>
                {customer && (
                  <div className="flex flex-wrap items-center gap-2">
                    <label className="flex items-center gap-2 text-sm">
                      <span className="text-muted">Bot</span>
                      <button
                        role="switch"
                        aria-checked={customer.bot_active}
                        aria-label="Bot for this customer"
                        onClick={() => void act(() => inboxApi.setBot(platform, customer.psid, !customer.bot_active))}
                        className={cx("relative h-6 w-11 rounded-full transition focus:outline-none focus:ring-4 focus:ring-[var(--ring)]", customer.bot_active ? "bg-brand" : "bg-border")}
                      >
                        <span className={cx("absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all", customer.bot_active ? "left-[22px]" : "left-0.5")} />
                      </button>
                    </label>
                    {customer.handoff_requested && (
                      <Button variant="secondary" className="px-3 py-1.5" onClick={() => void act(() => inboxApi.resolveHandoff(platform, customer.psid, true))}>
                        Resolve and resume bot
                      </Button>
                    )}
                    <Button
                      variant="ghost"
                      className="px-3 py-1.5"
                      onClick={() => {
                        const next = window.prompt("Name for this customer (leave empty to reset)", customer.custom_name ?? "");
                        if (next !== null) void act(() => inboxApi.rename(platform, customer.psid, next.trim() || null));
                      }}
                    >
                      Rename
                    </Button>
                    <Button
                      variant="ghost"
                      className="px-3 py-1.5 text-danger"
                      onClick={() => {
                        if (window.confirm("Delete this entire chat history from your database? This cannot be undone.")) void act(() => inboxApi.clearHistory(platform, customer.psid));
                      }}
                    >
                      Clear history
                    </Button>
                  </div>
                )}
              </header>

              <div className="min-h-0 flex-1 space-y-4 overflow-y-auto bg-surface-2/40 px-4 py-4">
                {chatLoading && (
                  <div className="flex justify-center py-8 text-muted" role="status">
                    <Spinner className="h-5 w-5" />
                  </div>
                )}
                {chatError && <Alert tone="error">{chatError}</Alert>}
                {hasOlder && (
                  <div className="text-center">
                    <Button variant="secondary" className="px-3 py-1.5" onClick={loadOlder}>
                      Load older messages
                    </Button>
                  </div>
                )}
                {!chatLoading && !chatError && messages.length === 0 && <p className="py-8 text-center text-sm text-muted">No messages in this conversation.</p>}
                {grouped.map((g) => (
                  <div key={g.day} className="space-y-2">
                    <p className="text-center text-[11px] font-medium uppercase tracking-wide text-muted">{g.day}</p>
                    {g.items.map((m) => {
                      const mine = m.sender !== "customer";
                      return (
                        <div key={m.id} className={cx("group flex", mine ? "justify-end" : "justify-start")}>
                          <div className={cx("max-w-[80%] space-y-1", mine && "items-end")}>
                            <div
                              className={cx(
                                "whitespace-pre-wrap break-words rounded-2xl px-3.5 py-2 text-sm leading-relaxed shadow-sm",
                                m.sender === "customer" && "rounded-bl-md border border-border bg-surface",
                                m.sender === "agent" && "rounded-br-md bg-brand text-white",
                                m.sender === "bot" && "rounded-br-md bg-surface-2 text-foreground ring-1 ring-border",
                              )}
                            >
                              {m.media_url && <MediaMessage message={m} />}
                              {m.text && <p className={m.media_url ? "mt-2" : undefined}>{m.text}</p>}
                            </div>
                            <p className={cx("flex items-center gap-2 text-[11px] text-muted", mine && "justify-end")}>
                              {m.sender === "bot" ? "Bot" : m.sender === "agent" ? "You" : ""}
                              {m.sender !== "customer" && " · "}
                              {new Date(m.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                              <button
                                className="opacity-0 transition hover:text-danger focus:opacity-100 group-hover:opacity-100"
                                onClick={() => {
                                  if (window.confirm("Delete this message from your database? It is not removed from the customer's phone.")) void act(() => inboxApi.deleteMessage(platform, m.id));
                                }}
                              >
                                Delete
                              </button>
                            </p>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ))}
                <div ref={bottomRef} />
              </div>

              <footer className="space-y-2 border-t border-border p-3">
                {actionError && <Alert tone="error">{actionError}</Alert>}
                {customer?.bot_active && <p className="text-xs text-muted">The bot is on for this customer. Sending a message may pause it, depending on your channel settings.</p>}
                <div className="flex items-end gap-2">
                  <input ref={fileRef} type="file" accept="image/jpeg,image/png" className="hidden" onChange={(e) => void sendImage(e.target.files?.[0])} />
                  <Button variant="secondary" className="px-3" onClick={() => fileRef.current?.click()} disabled={sending} aria-label="Send an image">
                    📎
                  </Button>
                  <textarea
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={onComposerKey}
                    rows={1}
                    maxLength={4000}
                    placeholder="Write a reply. Enter to send, Shift+Enter for a new line"
                    aria-label="Message"
                    className="max-h-40 min-h-[2.75rem] flex-1 resize-y rounded-lg border border-border bg-surface px-3 py-2.5 text-sm focus:border-brand focus:outline-none focus:ring-4 focus:ring-[var(--ring)]"
                  />
                  <Button onClick={() => void sendText()} loading={sending} disabled={!draft.trim()}>
                    Send
                  </Button>
                </div>
              </footer>
            </>
          )}
        </section>
      </div>
    </div>
  );
}
