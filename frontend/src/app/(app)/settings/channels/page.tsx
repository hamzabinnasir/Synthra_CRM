"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Card } from "@/components/ui";
import { Badge, PageHeader, Skeleton } from "@/components/form";
import { channelStatus } from "@/lib/channel-status";
import { channelsApi, errorText, type ChannelView } from "@/lib/settings-api";


export default function ChannelsOverviewPage() {
  const [channels, setChannels] = useState<ChannelView[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setChannels((await channelsApi.list()).channels);
    } catch (e) {
      setError(errorText(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Channels"
        description="Set up only the channels you need. Each one has its own credentials and bot behaviour. There are three webhooks in total (WhatsApp, Facebook and Instagram); inbox and comments on the same platform share one."
      />

      {error && (
        <div className="space-y-3">
          <Alert tone="error">{error}</Alert>
          <Button variant="secondary" onClick={() => void load()}>
            Try again
          </Button>
        </div>
      )}

      {!channels && !error && (
        <div className="grid gap-4 sm:grid-cols-2" role="status" aria-label="Loading channels">
          {Array.from({ length: 5 }, (_, i) => (
            <Skeleton key={i} className="h-36" />
          ))}
        </div>
      )}

      {channels && (
        <ul className="grid gap-4 sm:grid-cols-2">
          {channels.map((c) => {
            const s = channelStatus(c);
            return (
              <li key={c.id}>
                <Link href={`/settings/channels/${c.id}`} className="block h-full rounded-xl focus:outline-none focus:ring-4 focus:ring-[var(--ring)]">
                  <Card className="h-full p-5 transition hover:border-brand/50 hover:shadow-md">
                    <div className="flex items-start justify-between gap-3">
                      <h2 className="font-semibold">{c.title}</h2>
                      <Badge tone={s.tone}>{s.label}</Badge>
                    </div>
                    <p className="mt-2 text-sm leading-relaxed text-muted">{c.summary}</p>
                    <p className="mt-3 text-xs text-muted">
                      {c.status === "not_configured"
                        ? `${c.missing.length} required setting${c.missing.length === 1 ? "" : "s"} to fill in`
                        : `Bot ${c.bot_enabled ? "on" : "off"}${c.last_tested_at ? ` · tested ${new Date(c.last_tested_at).toLocaleDateString()}` : ""}`}
                    </p>
                  </Card>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
