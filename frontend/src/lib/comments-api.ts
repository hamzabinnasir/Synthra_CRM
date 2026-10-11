/**
 * Typed wrappers for /api/comments (backend/app/api/comments.py). Deleting is platform-first on the server:
 * a comment disappears from the list only after Meta confirmed the deletion.
 */
import { api } from "./api";

export type CommentPlatform = "facebook" | "instagram";
export const COMMENT_PLATFORMS: readonly CommentPlatform[] = ["facebook", "instagram"];

export const COMMENT_LABEL: Record<CommentPlatform, string> = { facebook: "Facebook", instagram: "Instagram" };

/** Channel settings page that must be configured for this platform's comments. */
export const COMMENT_CHANNEL: Record<CommentPlatform, string> = { facebook: "facebook_comments", instagram: "instagram_comments" };

export function isCommentPlatform(value: string): value is CommentPlatform {
  return (COMMENT_PLATFORMS as readonly string[]).includes(value);
}

/** Values the bot's classifier writes to `classification` (the comment's tone). */
export const CLASSIFICATIONS = ["friendly", "neutral", "confused", "frustrated", "angry", "sarcastic", "off_topic", "spam", "abusive"] as const;

export interface CommentItem {
  platform: string;
  comment_id: string;
  post_id: string | null;
  parent_id: string | null;
  from_id: string | null;
  from_name: string | null;
  from_username: string | null;
  message: string;
  classification: string | null;
  topic: string | null;
  action: string | null;
  bot_reply: string | null;
  bot_active: boolean;
  hidden: boolean;
  is_read: boolean;
  created_at: string;
}

export interface CommentQuery {
  q?: string;
  hidden?: boolean;
  unread?: boolean;
  classification?: string;
  before?: string;
  limit?: number;
}

export interface BulkDeleteResult {
  deleted: number;
  results: Array<{ id: string; ok: boolean; error?: string }>;
}

const base = (p: CommentPlatform) => `/api/comments/${p}`;
const one = (p: CommentPlatform, id: string) => `${base(p)}/${encodeURIComponent(id)}`;

export const commentsApi = {
  list: (p: CommentPlatform, query: CommentQuery = {}) =>
    api<{ comments: CommentItem[] }>(base(p), {
      query: {
        q: query.q || undefined,
        hidden: query.hidden,
        unread: query.unread || undefined,
        classification: query.classification || undefined,
        before: query.before,
        limit: query.limit,
      },
    }),
  markRead: (p: CommentPlatform, ids: string[]) => api<{ updated: number }>(`${base(p)}/read`, { method: "POST", body: { ids } }),
  reply: (p: CommentPlatform, id: string, text: string, mode: "public" | "private") =>
    api<{ ok: true }>(`${one(p, id)}/reply`, { method: "POST", body: { text, mode } }),
  hide: (p: CommentPlatform, id: string, hidden: boolean) => api<null>(`${one(p, id)}/hide`, { method: "POST", body: { hidden } }),
  setBot: (p: CommentPlatform, id: string, active: boolean) => api<null>(`${one(p, id)}/bot`, { method: "POST", body: { active } }),
  remove: (p: CommentPlatform, id: string) => api<null>(one(p, id), { method: "DELETE" }),
  bulkDelete: (p: CommentPlatform, ids: string[]) => api<BulkDeleteResult>(`${base(p)}/bulk-delete`, { method: "POST", body: { ids } }),
};
