/** Response shapes of the backend. Keep in sync with backend/app/tenantdb/* and backend/app/channels/*. */

/* ----------------------------------- database ----------------------------------- */

export type DbStatus = "connected" | "migrated" | "error";

export interface DbInfo {
  connected: boolean;
  latest_version: string;
  host_hint?: string;
  status?: DbStatus;
  last_checked_at?: string | null;
  last_error?: string | null;
  schema_version?: string | null;
  up_to_date?: boolean;
}

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

export type DbTestResult = Preflight & { host_hint: string; pending_migrations: number };

export interface MigrationStatus {
  version: string;
  name: string;
  state: "applied" | "pending";
}

export interface PendingMigration {
  version: string;
  name: string;
  sql: string;
}

export type MigrateResult =
  | { dry_run: true; pending: PendingMigration[] }
  | ({ dry_run: false; applied: string[] } & DbInfo);

/* ----------------------------------- channels ----------------------------------- */

export type ChannelStatus = "not_configured" | "configured" | "connected" | "error";
export type Platform = "whatsapp" | "facebook" | "instagram";

export interface FieldSpec {
  key: string;
  label: string;
  help: string;
  secret: boolean;
  required: boolean;
  placeholder: string;
}

/** Secrets only ever come back masked; plain ids come back as-is. */
export type FieldValue = { saved: boolean; masked: string } | { saved: boolean; value: string };

export interface Behaviour {
  llm_provider: string;
  llm_model: string;
  system_prompt: string;
  tone: string;
  spam_action: string;
  abuse_action: string;
  business_phone: string;
  pause_on_human: string; // "true" | "false"
  handoff_message: string;
  fallback_message: string;
}

export interface Channel {
  id: string;
  title: string;
  platform: Platform;
  kind: "inbox" | "comments";
  summary: string;
  webhook_fields: string[];
  guide: string;
  fields: FieldSpec[];
  enabled: boolean;
  bot_enabled: boolean;
  status: ChannelStatus;
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

export interface SaveChannelInput {
  values: Record<string, string>;
  behaviour: Record<string, string>;
  enabled?: boolean;
  bot_enabled?: boolean;
}

/* The option lists mirror backend/app/channels/registry.py (LLM_PROVIDERS, TONES, MODERATION_ACTIONS). */
export const LLM_PROVIDERS = [
  { value: "anthropic", label: "Anthropic (Claude)" },
  { value: "openai", label: "OpenAI" },
  { value: "gemini", label: "Google Gemini" },
  { value: "groq", label: "Groq" },
] as const;

export const TONES = [
  { value: "friendly", label: "Friendly" },
  { value: "professional", label: "Professional" },
  { value: "concise", label: "Concise" },
  { value: "playful", label: "Playful" },
  { value: "formal", label: "Formal" },
] as const;

export const MODERATION_ACTIONS = [
  { value: "none", label: "Do nothing" },
  { value: "hide", label: "Hide the comment" },
  { value: "delete", label: "Delete the comment" },
] as const;

export const SYSTEM_PROMPT_MAX = 8000;
export const CANNED_MAX = 500;
