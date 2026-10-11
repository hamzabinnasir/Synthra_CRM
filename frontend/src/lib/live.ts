"use client";

/**
 * Live updates from GET /api/events (server-sent events).
 *
 * The browser's EventSource cannot send an Authorization header, so this reads the stream with fetch. The
 * server only sends counters ("marks": newest message id, unread total, ...), never message text; when they
 * change, a page refetches what it shows. The server ends each stream after ~9 minutes and the loop reconnects
 * with a fresh token. Failures back off up to 30 s; the stream is aborted on unmount.
 */
import { useEffect, useState } from "react";
import { API_BASE, getAccessToken, refreshSession } from "./api";

export type Marks = Record<string, number>;

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

function parseBlock(block: string): { event: string; data: string } {
  let event = "message";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trim());
  }
  return { event, data: data.join("\n") };
}

/** Returns the latest counters (null until the first event). A new object is produced on every change. */
export function useLiveMarks(enabled = true): Marks | null {
  const [marks, setMarks] = useState<Marks | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let stopped = false;
    const controller = new AbortController();

    async function run() {
      let delay = 2000;
      while (!stopped) {
        try {
          const token = getAccessToken();
          const res = await fetch(`${API_BASE}/api/events`, {
            credentials: "include",
            headers: { Accept: "text/event-stream", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
            signal: controller.signal,
          });
          if (res.status === 401) {
            if (!(await refreshSession())) return; // signed out: the auth guard takes over
            continue;
          }
          if (!res.ok || !res.body) throw new Error(`stream ${res.status}`);
          delay = 2000;
          const reader = res.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            let cut: number;
            while ((cut = buffer.indexOf("\n\n")) >= 0) {
              const { event, data } = parseBlock(buffer.slice(0, cut));
              buffer = buffer.slice(cut + 2);
              if (event === "marks" && data && !stopped) {
                try {
                  setMarks(JSON.parse(data) as Marks);
                } catch {
                  /* ignore a malformed frame */
                }
              }
            }
          }
        } catch {
          if (stopped) return;
        }
        if (stopped) return;
        await sleep(delay);
        delay = Math.min(delay * 2, 30_000);
      }
    }

    void run();
    return () => {
      stopped = true;
      controller.abort();
    };
  }, [enabled]);

  return marks;
}
