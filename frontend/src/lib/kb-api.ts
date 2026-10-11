/**
 * Typed wrappers for /api/kb (backend/app/api/kb.py). Indexing is change-aware on the server: re-sending the same
 * text is a no-op ("unchanged"), and a changed document re-embeds only its new chunks.
 */
import { api } from "./api";

export interface KbDocument {
  id: number;
  source: string;
  title: string;
  size_bytes: number;
  chunk_count: number;
  created_at: string;
  updated_at: string;
}

export interface KbOverview {
  documents: KbDocument[];
  stats: { documents: number; chunks: number };
}

export type KbIndexStatus = "created" | "updated" | "unchanged";

export interface KbIndexResult {
  status: KbIndexStatus;
  id: number;
  chunks: number;
  embedded: number;
  reused: number;
}

export interface KbSearchHit {
  content: string;
  title: string;
  document_id: number;
  similarity: number;
}

export const kbApi = {
  overview: () => api<KbOverview>("/api/kb"),
  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return api<KbIndexResult>("/api/kb/upload", { method: "POST", form });
  },
  addText: (title: string, text: string) => api<KbIndexResult>("/api/kb/text", { method: "POST", body: { title, text } }),
  search: (query: string) => api<{ results: KbSearchHit[] }>("/api/kb/search", { method: "POST", body: { query } }),
  remove: (id: number) => api<null>(`/api/kb/${id}`, { method: "DELETE" }),
};

/** Human summary of an indexing result, e.g. "Updated: 2 new chunks embedded, 5 reused." */
export function describeIndexResult(r: KbIndexResult): string {
  if (r.status === "unchanged") return "No changes: that document is already indexed.";
  const verb = r.status === "created" ? "Added" : "Updated";
  return `${verb}: ${r.chunks} chunk${r.chunks === 1 ? "" : "s"} (${r.embedded} newly embedded, ${r.reused} reused).`;
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}
