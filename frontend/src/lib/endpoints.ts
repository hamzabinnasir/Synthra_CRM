import { api } from "./api";
import type {
  Channel,
  ChannelTestResult,
  DbInfo,
  DbTestResult,
  MigrateResult,
  MigrationStatus,
  SaveChannelInput,
} from "./types";

export const databaseApi = {
  info: () => api<DbInfo>("/api/database"),
  test: (url: string) => api<DbTestResult>("/api/database/test", { method: "POST", body: { url } }),
  save: (url: string) => api<DbInfo & { preflight: DbTestResult }>("/api/database", { method: "PUT", body: { url } }),
  migrate: (dry_run: boolean) => api<MigrateResult>("/api/database/migrate", { method: "POST", body: { dry_run } }),
  migrations: () => api<{ migrations: MigrationStatus[] }>("/api/database/migrations"),
  check: () => api<DbInfo & { migrations: MigrationStatus[] }>("/api/database/check", { method: "POST" }),
  disconnect: () => api<null>("/api/database", { method: "DELETE" }),
  /** The manual-fallback SQL script (contains no secrets). */
  manualSql: () => api<string>("/api/database/setup.sql"),
};

export const channelsApi = {
  list: () => api<{ channels: Channel[] }>("/api/channels"),
  get: (id: string) => api<Channel>(`/api/channels/${encodeURIComponent(id)}`),
  save: (id: string, input: SaveChannelInput) =>
    api<Channel>(`/api/channels/${encodeURIComponent(id)}`, { method: "PUT", body: input }),
  test: (id: string) => api<ChannelTestResult>(`/api/channels/${encodeURIComponent(id)}/test`, { method: "POST" }),
  rotateVerifyToken: (id: string) =>
    api<Channel>(`/api/channels/${encodeURIComponent(id)}/verify-token/rotate`, { method: "POST" }),
  clear: (id: string) => api<null>(`/api/channels/${encodeURIComponent(id)}`, { method: "DELETE" }),
};

/** Save text as a file in the browser (used for the manual setup SQL). */
export function downloadText(filename: string, text: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
