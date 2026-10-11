/**
 * Typed wrappers for /api/analytics, /api/notifications and /api/activity (backend/app/api/insights.py).
 * Shapes mirror crm/store.py (analytics, list_notifications).
 */
import { api } from "./api";

export interface DailyPoint {
  date: string;
  customer: number;
  bot: number;
  agent: number;
}

export interface PlatformVolume {
  platform: string;
  customer: number;
  bot: number;
  agent: number;
}

export interface Analytics {
  days: number;
  daily: DailyPoint[];
  by_platform: PlatformVolume[];
  response_time: { samples: number; avg_seconds: number | null; p50_seconds: number | null; p90_seconds: number | null };
  handoff: { count: number; conversations: number; open: number; rate: number };
  comments: {
    by_classification: Array<{ classification: string; n: number }>;
    by_action: Array<{ action: string; n: number }>;
  };
  totals: { unread_conversations: number; unread_comments: number; kb_documents: number };
}

export interface NotificationItem {
  id: number;
  type: string;
  platform: string;
  source_type: string;
  customer_id: string | null;
  customer_name: string | null;
  customer_username: string | null;
  target_id: string;
  message_preview: string;
  is_read: boolean;
  metadata: Record<string, unknown>;
  created_at: string;
}

export const ANALYTICS_RANGES = [7, 14, 30, 90] as const;

export const insightsApi = {
  analytics: (days: number) => api<Analytics>("/api/analytics", { query: { days } }),
  notifications: (opts: { unread?: boolean; beforeId?: number; limit?: number } = {}) =>
    api<{ notifications: NotificationItem[]; unread: number }>("/api/notifications", {
      query: { unread: opts.unread || undefined, before_id: opts.beforeId, limit: opts.limit },
    }),
  unreadCount: () => api<{ unread: number }>("/api/notifications/unread-count"),
  /** Omit `ids` to mark everything read. */
  markRead: (ids?: number[]) => api<{ updated: number }>("/api/notifications/read", { method: "POST", body: ids ? { ids } : {} }),
};

export function formatSeconds(value: number | null): string {
  if (value === null) return "–";
  if (value < 60) return `${value.toFixed(1)} s`;
  const minutes = value / 60;
  return minutes < 60 ? `${minutes.toFixed(1)} min` : `${(minutes / 60).toFixed(1)} h`;
}

export function formatPercent(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`;
}

const rtf = typeof Intl !== "undefined" ? new Intl.RelativeTimeFormat(undefined, { numeric: "auto" }) : null;

/** "5 minutes ago" style label; falls back to the ISO date. */
export function timeAgo(iso: string): string {
  const diff = (new Date(iso).getTime() - Date.now()) / 1000;
  if (!rtf || Number.isNaN(diff)) return iso;
  const abs = Math.abs(diff);
  if (abs < 60) return rtf.format(Math.round(diff), "second");
  if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour");
  return rtf.format(Math.round(diff / 86400), "day");
}
