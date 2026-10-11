/**
 * Typed wrappers for /api/inbox plus authenticated media loading. Shapes mirror backend/app/api/inbox.py and
 * crm/store.py (list_conversations, get_messages). Messages come back oldest first.
 */
import { api, ApiError, API_BASE, getAccessToken, refreshSession } from "./api";

export type InboxPlatform = "whatsapp" | "facebook" | "instagram";
export const INBOX_PLATFORMS: readonly InboxPlatform[] = ["whatsapp", "facebook", "instagram"];

export const PLATFORM_LABEL: Record<InboxPlatform, string> = {
  whatsapp: "WhatsApp",
  facebook: "Facebook",
  instagram: "Instagram",
};

/** Settings page that has to be configured before this inbox can work. */
export const PLATFORM_CHANNEL: Record<InboxPlatform, string> = {
  whatsapp: "whatsapp",
  facebook: "facebook_inbox",
  instagram: "instagram_inbox",
};

export function isInboxPlatform(value: string): value is InboxPlatform {
  return (INBOX_PLATFORMS as readonly string[]).includes(value);
}

export interface Conversation {
  psid: string;
  platform: string;
  name: string | null;
  custom_name: string | null;
  username: string | null;
  profile_pic_url: string | null;
  bot_active: boolean;
  comment_bot_active: boolean;
  handoff_requested: boolean;
  handoff_at: string | null;
  last_message_text: string | null;
  last_customer_message_at: string | null;
  last_activity_at: string | null;
  unread_count: number;
  display_name: string;
}

export interface ChatMessage {
  id: number;
  psid: string;
  platform: string;
  sender: "customer" | "bot" | "agent";
  text: string | null;
  comment_id: string | null;
  mid: string | null;
  /** "media:<id>" for files stored by Synthra. */
  media_url: string | null;
  media_type: string | null;
  media_mime_type: string | null;
  media_filename: string | null;
  media_status: string | null;
  created_at: string;
}

export interface ConversationQuery {
  q?: string;
  unread?: boolean;
  handoff?: boolean;
  before?: string;
  limit?: number;
}

const base = (p: InboxPlatform) => `/api/inbox/${p}`;
const conv = (p: InboxPlatform, psid: string) => `${base(p)}/conversations/${encodeURIComponent(psid)}`;

export const inboxApi = {
  conversations: (p: InboxPlatform, query: ConversationQuery = {}) =>
    api<{ conversations: Conversation[] }>(`${base(p)}/conversations`, {
      query: { q: query.q || undefined, unread: query.unread || undefined, handoff: query.handoff || undefined, before: query.before, limit: query.limit },
    }),
  messages: (p: InboxPlatform, psid: string, beforeId?: number) =>
    api<{ customer: Conversation; messages: ChatMessage[] }>(`${conv(p, psid)}/messages`, { query: { before_id: beforeId, limit: 100 } }),
  markRead: (p: InboxPlatform, psid: string) => api<null>(`${conv(p, psid)}/read`, { method: "POST" }),
  send: (p: InboxPlatform, psid: string, text: string) => api<{ id: number | null }>(`${conv(p, psid)}/send`, { method: "POST", body: { text } }),
  sendImage: (p: InboxPlatform, psid: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return api<{ id: number | null }>(`${conv(p, psid)}/send-image`, { method: "POST", form });
  },
  setBot: (p: InboxPlatform, psid: string, active: boolean) => api<null>(`${conv(p, psid)}/bot`, { method: "POST", body: { active } }),
  resolveHandoff: (p: InboxPlatform, psid: string, resumeBot: boolean) =>
    api<null>(`${conv(p, psid)}/handoff/resolve`, { method: "POST", body: { resume_bot: resumeBot } }),
  rename: (p: InboxPlatform, psid: string, name: string | null) => api<null>(conv(p, psid), { method: "PATCH", body: { name } }),
  clearHistory: (p: InboxPlatform, psid: string) => api<unknown>(`${conv(p, psid)}/history`, { method: "DELETE" }),
  deleteMessage: (p: InboxPlatform, id: number) => api<null>(`${base(p)}/messages/${id}`, { method: "DELETE" }),
};

/** True when the backend says the database is not connected or not set up yet. */
export function isSetupError(error: unknown): boolean {
  return error instanceof ApiError && (error.code === "not_connected" || error.code === "not_migrated");
}

/** /api/media needs the Bearer header, which <img src> cannot send, so fetch it and hand back a Blob. */
export async function fetchMediaBlob(id: string, signal?: AbortSignal): Promise<Blob> {
  const get = () =>
    fetch(`${API_BASE}/api/media/${encodeURIComponent(id)}`, {
      credentials: "include",
      headers: getAccessToken() ? { Authorization: `Bearer ${getAccessToken()}` } : {},
      signal,
    });
  let res = await get();
  if (res.status === 401 && (await refreshSession())) res = await get();
  if (!res.ok) throw new ApiError(res.status, "media_unavailable", "This file could not be loaded.");
  return res.blob();
}
