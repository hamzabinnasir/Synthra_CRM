/**
 * Typed wrappers for the setup endpoints (/api/database, /api/channels). Shapes mirror the backend:
 * backend/app/api/database.py, backend/app/channels/service.py and backend/app/channels/registry.py.
 * Secrets are never returned by the API: secret fields only carry {saved, masked}.
 */
import { api, ApiError } from "./api";

/* -------------------------------- shared helpers -------------------------------- */

/** One human-readable line for any thrown error, including the backend's optional hint. */
export function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.hint ? `${error.message} ${error.hint}` : error.message;
  return "Something unexpected happened. Please try again.";
}

/* ----------------------------------- database ----------------------------------- */

export interface Preflight {
  ok: boolean;
  server_version: string;
  database: string;
  user: string;
  can_create_schema: boolean;
  vector_installed: boolean;
  vector_available: boolean;
  problems: string[];
  warnings: string[];
}

export interface DbInfo {
  connected: boolean;
  latest_version: string | null;
  host_hint?: string;
  /** connected | migrated | error */
  status?: string;
  last_checked_at?: string | null;
  last_error?: string | null;
  schema_version?: string | null;
  up_to_date?: boolean;
}

export type DbTestResult = Preflight & { host_hint: string; pending_migrations: number };
export interface MigrationRow {
  version: string;
  name: string;
  state: string;
}
export interface PendingMigration {
  version: string;
  name: string;
  sql: string;
}

export const databaseApi = {
  info: () => api<DbInfo>("/api/database"),
  test: (url: string) => api<DbTestResult>("/api/database/test", { method: "POST", body: { url } }),
  save: (url: string) => api<DbInfo & { preflight: Preflight }>("/api/database", { method: "PUT", body: { url } }),
  preview: () => api<{ dry_run: true; pending: PendingMigration[] }>("/api/database/migrate", { method: "POST", body: { dry_run: true } }),
  migrate: () => api<DbInfo & { dry_run: false; applied: string[] }>("/api/database/migrate", { method: "POST", body: { dry_run: false } }),
  migrations: () => api<{ migrations: MigrationRow[] }>("/api/database/migrations"),
  check: () => api<DbInfo & { migrations: MigrationRow[] }>("/api/database/check", { method: "POST" }),
  manualSql: () => api<string>("/api/database/setup.sql"),
  disconnect: () => api<null>("/api/database", { method: "DELETE" }),
};

/* ----------------------------------- channels ----------------------------------- */

export const LLM_PROVIDERS = [
  { value: "anthropic", label: "Anthropic (Claude)" },
  { value: "openai", label: "OpenAI" },
  { value: "gemini", label: "Google Gemini" },
  { value: "groq", label: "Groq" },
] as const;

export const TONES = ["friendly", "professional", "concise", "playful", "formal"] as const;

export const MODERATION_ACTIONS = [
  { value: "none", label: "Do nothing" },
  { value: "hide", label: "Hide it" },
  { value: "delete", label: "Delete it" },
] as const;

export interface ChannelField {
  key: string;
  label: string;
  help: string;
  secret: boolean;
  required: boolean;
  placeholder: string;
}

export interface FieldValue {
  saved: boolean;
  /** Secret fields: a masked preview only. */
  masked?: string;
  /** Non-secret fields: the stored value. */
  value?: string;
}

export type Behaviour = Record<string, string>;

export interface ChannelView {
  id: string;
  title: string;
  platform: "whatsapp" | "facebook" | "instagram";
  kind: "inbox" | "comments";
  summary: string;
  webhook_fields: string[];
  /** Repo-relative path of this channel's setup guide. */
  guide: string;
  fields: ChannelField[];
  enabled: boolean;
  bot_enabled: boolean;
  /** not_configured | configured | connected | error */
  status: string;
  last_tested_at: string | null;
  last_error: string | null;
  values: Record<string, FieldValue>;
  missing: string[];
  behaviour: Behaviour;
  webhook: { url: string; verify_token: string; subscribe_to: string[] };
}

export interface ChannelTestResult {
  ok: boolean;
  message: string;
  details: Record<string, unknown>;
  warnings: string[];
}

export interface ChannelSave {
  /** Only fields the user typed. Empty or missing means "keep what is saved". */
  values?: Record<string, string>;
  behaviour?: Behaviour;
  enabled?: boolean;
  bot_enabled?: boolean;
}

export const channelsApi = {
  list: () => api<{ channels: ChannelView[] }>("/api/channels"),
  get: (id: string) => api<ChannelView>(`/api/channels/${encodeURIComponent(id)}`),
  save: (id: string, body: ChannelSave) => api<ChannelView>(`/api/channels/${encodeURIComponent(id)}`, { method: "PUT", body }),
  test: (id: string) => api<ChannelTestResult>(`/api/channels/${encodeURIComponent(id)}/test`, { method: "POST" }),
  rotateVerifyToken: (id: string) => api<ChannelView>(`/api/channels/${encodeURIComponent(id)}/verify-token/rotate`, { method: "POST" }),
  clear: (id: string) => api<null>(`/api/channels/${encodeURIComponent(id)}`, { method: "DELETE" }),
};

/** Where the written guides are served. Set NEXT_PUBLIC_DOCS_URL (e.g. your repo's docs folder URL) once they are published. */
export const DOCS_URL = (process.env.NEXT_PUBLIC_DOCS_URL ?? "").replace(/\/$/, "");
