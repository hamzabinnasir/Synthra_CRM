"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Alert, Button, Card, Input, cx } from "@/components/ui";
import { Badge, PageHeader, SectionCard, Skeleton, Textarea } from "@/components/form";
import { isSetupError } from "@/lib/inbox-api";
import { describeIndexResult, formatBytes, kbApi, type KbIndexResult, type KbOverview, type KbSearchHit } from "@/lib/kb-api";
import { timeAgo } from "@/lib/insights-api";
import { errorText } from "@/lib/settings-api";

type Mode = "upload" | "text";

export function KnowledgeBaseView() {
  const [data, setData] = useState<KbOverview | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);

  const [mode, setMode] = useState<Mode>("upload");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");

  const [deleting, setDeleting] = useState<number | null>(null);
  const [confirmId, setConfirmId] = useState<number | null>(null);

  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [hits, setHits] = useState<KbSearchHit[] | null>(null);
  const [searchError, setSearchError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await kbApi.overview());
      setLoadError(null);
    } catch (e) {
      setLoadError(e);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      let result: KbIndexResult;
      if (mode === "upload") {
        if (!file) {
          setError("Choose a file first.");
          return;
        }
        result = await kbApi.upload(file);
        setFile(null);
        if (fileRef.current) fileRef.current.value = "";
      } else {
        result = await kbApi.addText(title.trim(), text.trim());
        if (result.status !== "unchanged") {
          setTitle("");
          setText("");
        }
      }
      setNotice(describeIndexResult(result));
      await load();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: number) {
    setDeleting(id);
    setError(null);
    setNotice(null);
    try {
      await kbApi.remove(id);
      setConfirmId(null);
      setHits((h) => (h ? h.filter((x) => x.document_id !== id) : h));
      await load();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setDeleting(null);
    }
  }

  async function runSearch(e: FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;
    setSearching(true);
    setSearchError(null);
    try {
      setHits((await kbApi.search(query.trim())).results);
    } catch (err) {
      setHits(null);
      setSearchError(errorText(err));
    } finally {
      setSearching(false);
    }
  }

  if (isSetupError(loadError)) {
    return (
      <div className="mx-auto max-w-lg space-y-4 py-16 text-center">
        <h1 className="text-xl font-semibold">Connect your database first</h1>
        <p className="text-sm text-muted">Your knowledge base is stored in your own database. Connect it and run the setup to start.</p>
        <Link href="/settings/database" className="inline-block rounded-lg bg-brand px-4 py-2.5 text-sm font-medium text-white hover:bg-brand-hover">
          Go to database setup
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Knowledge base"
        description="Documents the bot uses to answer customers. Text is split into chunks and embedded automatically. Re-uploading a changed document only re-embeds the new parts, and the bot keeps answering from the old version until the new one is ready."
        actions={
          data && (
            <>
              <Badge tone="info">{data.stats.documents} document{data.stats.documents === 1 ? "" : "s"}</Badge>
              <Badge tone="neutral">{data.stats.chunks} chunks</Badge>
            </>
          )
        }
      />

      {loadError !== null && !isSetupError(loadError) && <Alert tone="error">{errorText(loadError)}</Alert>}
      {error && <Alert tone="error">{error}</Alert>}
      {notice && <Alert tone="success">{notice}</Alert>}

      <SectionCard title="Add content" description="Upload a PDF or text file, or paste text. Using the same file name or title again updates that document.">
        <form onSubmit={submit} className="space-y-4">
          <div className="inline-flex rounded-lg border border-border bg-surface-2 p-1 text-sm" role="tablist" aria-label="Add content as">
            {(["upload", "text"] as const).map((m) => (
              <button
                key={m}
                type="button"
                role="tab"
                aria-selected={mode === m}
                onClick={() => setMode(m)}
                className={cx("rounded-md px-3 py-1.5 font-medium transition", mode === m ? "bg-surface shadow-sm" : "text-muted hover:text-foreground")}
              >
                {m === "upload" ? "Upload file" : "Paste text"}
              </button>
            ))}
          </div>

          {mode === "upload" ? (
            <div className="space-y-1.5">
              <label htmlFor="kb-file" className="block text-sm font-medium">
                File
              </label>
              <input
                id="kb-file"
                ref={fileRef}
                type="file"
                accept=".pdf,.txt,.md,.csv,text/plain,application/pdf"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                className="block w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm file:mr-3 file:rounded-md file:border-0 file:bg-surface-2 file:px-3 file:py-1.5 file:text-sm file:font-medium"
              />
              <p className="text-xs text-muted">PDF or plain text. The server rejects files over its upload limit with a clear message.</p>
            </div>
          ) : (
            <div className="space-y-4">
              <Input label="Title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={150} placeholder="Shipping and returns policy" required />
              <Textarea label="Content" value={text} onChange={(e) => setText(e.target.value)} rows={8} placeholder="Paste the text the bot should know…" required />
            </div>
          )}

          <Button type="submit" loading={busy} disabled={mode === "upload" ? !file : !title.trim() || !text.trim()}>
            {busy ? "Indexing…" : "Add to knowledge base"}
          </Button>
          {busy && <p className="text-xs text-muted">The first upload downloads the embedding model (about 90 MB), so it can take a minute.</p>}
        </form>
      </SectionCard>

      <SectionCard title="Documents">
        {data === null && loadError === null ? (
          <div className="space-y-2">
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
            <Skeleton className="h-12" />
          </div>
        ) : data && data.documents.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border px-4 py-10 text-center">
            <p className="font-medium">Nothing here yet</p>
            <p className="mt-1 text-sm text-muted">Add a document above and the bot can start using it to answer questions.</p>
          </div>
        ) : (
          <ul className="divide-y divide-border rounded-lg border border-border">
            {data?.documents.map((d) => (
              <li key={d.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">{d.title || d.source}</p>
                  <p className="text-xs text-muted">
                    {d.chunk_count} chunk{d.chunk_count === 1 ? "" : "s"} · {formatBytes(d.size_bytes)} · updated {timeAgo(d.updated_at)}
                  </p>
                </div>
                {confirmId === d.id ? (
                  <div className="flex items-center gap-2">
                    <span className="text-sm text-muted">Delete this document?</span>
                    <Button variant="danger" className="px-3 py-1.5" loading={deleting === d.id} onClick={() => void remove(d.id)}>
                      Delete
                    </Button>
                    <Button variant="ghost" className="px-3 py-1.5" onClick={() => setConfirmId(null)} disabled={deleting === d.id}>
                      Cancel
                    </Button>
                  </div>
                ) : (
                  <Button variant="ghost" className="px-3 py-1.5" onClick={() => setConfirmId(d.id)} aria-label={`Delete ${d.title || d.source}`}>
                    Delete
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </SectionCard>

      <SectionCard title="Test retrieval" description="See which passages the bot would find for a customer question.">
        <form onSubmit={runSearch} className="flex flex-wrap items-end gap-3">
          <div className="min-w-[16rem] flex-1">
            <Input label="Question" value={query} onChange={(e) => setQuery(e.target.value)} maxLength={500} placeholder="What is your return policy?" />
          </div>
          <Button type="submit" variant="secondary" loading={searching} disabled={!query.trim()}>
            Search
          </Button>
        </form>
        {searchError && <Alert tone="error">{searchError}</Alert>}
        {hits && hits.length === 0 && <p className="text-sm text-muted">No passages found. Add documents first.</p>}
        {hits && hits.length > 0 && (
          <ul className="space-y-3">
            {hits.map((h, i) => (
              <li key={`${h.document_id}-${i}`}>
                <Card className="p-4">
                  <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
                    <span className="text-sm font-medium">{h.title}</span>
                    <Badge tone={h.similarity >= 0.5 ? "success" : h.similarity >= 0.3 ? "warning" : "neutral"}>{Math.round(h.similarity * 100)}% match</Badge>
                  </div>
                  <p className="whitespace-pre-wrap text-sm leading-relaxed text-muted">{h.content}</p>
                </Card>
              </li>
            ))}
          </ul>
        )}
        <p className="text-xs text-muted">The bot only uses passages above roughly 30% similarity.</p>
      </SectionCard>
    </div>
  );
}
