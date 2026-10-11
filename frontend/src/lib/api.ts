/**
 * Typed API client for the Synthra CRM backend.
 *
 * Security model (matches the backend):
 *  - The short-lived access token lives ONLY in memory (never localStorage/sessionStorage).
 *  - The rotating refresh token is an httpOnly cookie the browser sends automatically; cookie-authenticated
 *    endpoints (refresh, logout) also need the `X-CSRF-Protection: 1` header.
 *  - Refreshes are single-flight: the backend rotates refresh tokens, so two parallel refreshes would race.
 */

export const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export interface Session {
  access_token: string;
  expires_at: string;
  user_id: string;
  tenant_id: string;
}

export interface Me {
  user: { id: string; email: string; display_name: string; email_verified: boolean };
  tenant: { id: string; name: string };
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId?: string;
  /** Structured extras from the backend, e.g. {hint} for database errors or {fields: {key: message}} for settings. */
  readonly details?: unknown;

  constructor(status: number, code: string, message: string, requestId?: string, details?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.details = details;
  }

  /** Optional human hint (database setup errors). */
  get hint(): string | undefined {
    const d = this.details as { hint?: unknown } | null | undefined;
    return typeof d?.hint === "string" ? d.hint : undefined;
  }

  /** Per-field messages from `invalid_settings` ({fields: {...}}) or 422 `validation_error` ([{field,message}]). */
  get fieldErrors(): Record<string, string> {
    const d = this.details as unknown;
    if (Array.isArray(d)) {
      return Object.fromEntries(
        d.filter((e): e is { field: string; message: string } => typeof e?.field === "string").map((e) => [e.field, String(e.message)]),
      );
    }
    const fields = (d as { fields?: unknown } | null | undefined)?.fields;
    return fields && typeof fields === "object" ? (fields as Record<string, string>) : {};
  }
}

/* ---------------------------------- session state ---------------------------------- */

let accessToken: string | null = null;
let inflightRefresh: Promise<Session | null> | null = null;
const listeners = new Set<(session: Session | null) => void>();

export function getAccessToken(): string | null {
  return accessToken;
}

export function setSession(session: Session | null): void {
  accessToken = session?.access_token ?? null;
  listeners.forEach((fn) => fn(session));
}

export function onSessionChange(fn: (session: Session | null) => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

/* ------------------------------------- plumbing ------------------------------------- */

async function readBody(res: Response): Promise<unknown> {
  if (res.status === 204) return null;
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function toApiError(res: Response, body: unknown): ApiError {
  const err = (body as { error?: { code?: string; message?: string; request_id?: string; details?: unknown } } | null)?.error;
  const message = err?.message ?? (res.status === 429 ? "Too many attempts. Please wait and try again." : "Something went wrong.");
  return new ApiError(res.status, err?.code ?? `http_${res.status}`, message, err?.request_id, err?.details);
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function refreshOnce(): Promise<Session | null> {
  for (let attempt = 0; attempt < 2; attempt++) {
    let res: Response;
    try {
      res = await fetch(`${API_BASE}/api/auth/refresh`, {
        method: "POST",
        credentials: "include",
        headers: { "X-CSRF-Protection": "1" },
      });
    } catch {
      return null; // network error: stay signed-out in memory; the cookie is untouched
    }
    if (res.ok) {
      const session = (await res.json()) as Session;
      setSession(session);
      return session;
    }
    const body = await readBody(res);
    // Another tab just rotated the token: wait out the race once and retry with the fresh cookie.
    if (res.status === 401 && toApiError(res, body).code === "refresh_race" && attempt === 0) {
      await sleep(400);
      continue;
    }
    setSession(null);
    return null;
  }
  setSession(null);
  return null;
}

/** Exchange the refresh cookie for a new access token. Concurrent callers share one request. */
export function refreshSession(): Promise<Session | null> {
  if (!inflightRefresh) {
    const p = refreshOnce();
    inflightRefresh = p;
    p.finally(() => {
      if (inflightRefresh === p) inflightRefresh = null;
    });
  }
  return inflightRefresh;
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  /** JSON body (serialised for you). */
  body?: unknown;
  /** Multipart body (file uploads). */
  form?: FormData;
  /** Query-string parameters; undefined values are skipped. */
  query?: Record<string, string | number | boolean | undefined>;
  /** Send the Bearer access token (default true). Set false for public endpoints. */
  auth?: boolean;
  signal?: AbortSignal;
}

function buildUrl(path: string, query?: RequestOptions["query"]): string {
  const url = new URL(`${API_BASE}${path}`);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined) url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

async function send(path: string, opts: RequestOptions): Promise<Response> {
  const headers: Record<string, string> = {};
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (opts.auth !== false && accessToken) headers.Authorization = `Bearer ${accessToken}`;
  return fetch(buildUrl(path, opts.query), {
    method: opts.method ?? "GET",
    credentials: "include",
    headers,
    body: opts.form ?? (opts.body !== undefined ? JSON.stringify(opts.body) : undefined),
    signal: opts.signal,
  });
}

/** Call the API. On a 401 it refreshes the session once and retries; if that fails the user is signed out. */
export async function api<T = unknown>(path: string, opts: RequestOptions = {}): Promise<T> {
  let res: Response;
  try {
    res = await send(path, opts);
  } catch (error) {
    if ((error as Error).name === "AbortError") throw error;
    throw new ApiError(0, "network_error", "Cannot reach the server. Check your connection and try again.");
  }

  if (res.status === 401 && opts.auth !== false) {
    const session = await refreshSession();
    if (session) {
      try {
        res = await send(path, opts);
      } catch (error) {
        if ((error as Error).name === "AbortError") throw error;
        throw new ApiError(0, "network_error", "Cannot reach the server. Check your connection and try again.");
      }
    }
  }

  const body = await readBody(res);
  if (!res.ok) throw toApiError(res, body);
  return body as T;
}

/* ----------------------------------- auth endpoints ----------------------------------- */

export const authApi = {
  register: (input: { email: string; password: string; display_name: string; workspace_name: string }) =>
    api<{ message: string }>("/api/auth/register", { method: "POST", body: input, auth: false }),

  verifyEmail: (token: string) =>
    api<{ message: string }>("/api/auth/verify-email", { method: "POST", body: { token }, auth: false }),

  resendVerification: (email: string) =>
    api<{ message: string }>("/api/auth/resend-verification", { method: "POST", body: { email }, auth: false }),

  async login(email: string, password: string): Promise<Session> {
    const session = await api<Session>("/api/auth/login", { method: "POST", body: { email, password }, auth: false });
    setSession(session);
    return session;
  },

  async logout(): Promise<void> {
    try {
      await fetch(`${API_BASE}/api/auth/logout`, {
        method: "POST",
        credentials: "include",
        headers: { "X-CSRF-Protection": "1" },
      });
    } finally {
      setSession(null);
    }
  },

  async logoutAll(): Promise<void> {
    try {
      await api<null>("/api/auth/logout-all", { method: "POST" });
    } finally {
      setSession(null);
    }
  },

  forgotPassword: (email: string) =>
    api<{ message: string }>("/api/auth/forgot-password", { method: "POST", body: { email }, auth: false }),

  resetPassword: (token: string, new_password: string) =>
    api<{ message: string }>("/api/auth/reset-password", { method: "POST", body: { token, new_password }, auth: false }),

  me: () => api<Me>("/api/auth/me"),
};
