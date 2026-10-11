"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Alert, Card, cx } from "@/components/ui";
import { PageHeader, SectionCard, Skeleton } from "@/components/form";
import { isSetupError, PLATFORM_LABEL, type InboxPlatform } from "@/lib/inbox-api";
import {
  ANALYTICS_RANGES,
  formatPercent,
  formatSeconds,
  insightsApi,
  type Analytics,
  type DailyPoint,
} from "@/lib/insights-api";
import { useLiveMarks } from "@/lib/live";
import { errorText } from "@/lib/settings-api";

const human = (s: string) => {
  const t = s.replace(/_/g, " ");
  return t[0].toUpperCase() + t.slice(1);
};

const SERIES = [
  { key: "customer", label: "Customers", color: "var(--brand)" },
  { key: "bot", label: "Bot", color: "var(--success)" },
  { key: "agent", label: "Agents", color: "var(--warning)" },
] as const;

function Kpi({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card className="p-4">
      <p className="text-sm text-muted">{label}</p>
      <p className="mt-1 text-2xl font-semibold tracking-tight">{value}</p>
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </Card>
  );
}

function DailyChart({ daily }: { daily: DailyPoint[] }) {
  const max = Math.max(1, ...daily.map((d) => d.customer + d.bot + d.agent));
  const total = daily.reduce((n, d) => n + d.customer + d.bot + d.agent, 0);
  const dense = daily.length > 31;

  if (total === 0) {
    return <p className="rounded-lg border border-dashed border-border px-4 py-10 text-center text-sm text-muted">No messages in this period yet.</p>;
  }

  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-4 text-xs text-muted">
        {SERIES.map((s) => (
          <span key={s.key} className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-sm" style={{ background: s.color }} aria-hidden="true" />
            {s.label}
          </span>
        ))}
      </div>
      <div className="flex h-48 items-end gap-1" role="img" aria-label={`Messages per day, ${total} in total`}>
        {daily.map((d) => {
          const sum = d.customer + d.bot + d.agent;
          return (
            <div
              key={d.date}
              className="flex h-full min-w-0 flex-1 flex-col justify-end"
              title={`${d.date}: ${d.customer} customer, ${d.bot} bot, ${d.agent} agent`}
            >
              <div className="flex flex-col-reverse overflow-hidden rounded-t" style={{ height: `${(sum / max) * 100}%` }}>
                {SERIES.map((s) => (d[s.key] > 0 ? <div key={s.key} style={{ height: `${(d[s.key] / sum) * 100}%`, background: s.color }} /> : null))}
              </div>
            </div>
          );
        })}
      </div>
      <div className="mt-2 flex justify-between text-xs text-muted">
        <span>{daily[0].date}</span>
        {!dense && <span>peak {max}/day</span>}
        <span>{daily[daily.length - 1].date}</span>
      </div>
    </div>
  );
}

function Breakdown({ rows, empty }: { rows: Array<{ label: string; n: number }>; empty: string }) {
  const max = Math.max(1, ...rows.map((r) => r.n));
  if (rows.length === 0) return <p className="text-sm text-muted">{empty}</p>;
  return (
    <ul className="space-y-2.5">
      {rows.map((r) => (
        <li key={r.label} className="text-sm">
          <div className="mb-1 flex justify-between">
            <span>{r.label}</span>
            <span className="tabular-nums text-muted">{r.n}</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-surface-2">
            <div className="h-full rounded-full bg-brand" style={{ width: `${(r.n / max) * 100}%` }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

export function AnalyticsView() {
  const marks = useLiveMarks();
  const [days, setDays] = useState<number>(7);
  const [data, setData] = useState<Analytics | null>(null);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    try {
      setData(await insightsApi.analytics(days));
      setError(null);
    } catch (e) {
      setError(e);
    }
  }, [days]);

  useEffect(() => {
    setData(null);
    void load();
  }, [load]);

  useEffect(() => {
    if (!marks) return;
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per live event
  }, [marks]);

  if (isSetupError(error)) {
    return (
      <div className="mx-auto max-w-lg space-y-4 py-16 text-center">
        <h1 className="text-xl font-semibold">Connect your database first</h1>
        <p className="text-sm text-muted">Analytics are calculated from the data in your own database.</p>
        <Link href="/settings/database" className="inline-block rounded-lg bg-brand px-4 py-2.5 text-sm font-medium text-white hover:bg-brand-hover">
          Go to database setup
        </Link>
      </div>
    );
  }

  const totalMessages = data ? data.daily.reduce((n, d) => n + d.customer + d.bot + d.agent, 0) : 0;
  const botMessages = data ? data.daily.reduce((n, d) => n + d.bot, 0) : 0;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Analytics"
        description="Volume, bot response time and human handoffs across your channels."
        actions={
          <div className="inline-flex rounded-lg border border-border bg-surface-2 p-1 text-sm" role="group" aria-label="Time range">
            {ANALYTICS_RANGES.map((r) => (
              <button
                key={r}
                type="button"
                aria-pressed={days === r}
                onClick={() => setDays(r)}
                className={cx("rounded-md px-3 py-1.5 font-medium transition", days === r ? "bg-surface shadow-sm" : "text-muted hover:text-foreground")}
              >
                {r}d
              </button>
            ))}
          </div>
        }
      />

      {error !== null && <Alert tone="error">{errorText(error)}</Alert>}

      {!data && error === null ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
      ) : (
        data && (
          <>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <Kpi label="Messages" value={totalMessages.toLocaleString()} hint={`${botMessages.toLocaleString()} sent by the bot`} />
              <Kpi
                label="Median response time"
                value={formatSeconds(data.response_time.p50_seconds)}
                hint={
                  data.response_time.samples > 0
                    ? `p90 ${formatSeconds(data.response_time.p90_seconds)} · ${data.response_time.samples} replies`
                    : "No bot replies measured yet"
                }
              />
              <Kpi
                label="Handoff rate"
                value={formatPercent(data.handoff.rate)}
                hint={`${data.handoff.count} handoff${data.handoff.count === 1 ? "" : "s"} across ${data.handoff.conversations} conversation${data.handoff.conversations === 1 ? "" : "s"}`}
              />
              <Kpi label="Waiting for a human" value={String(data.handoff.open)} hint={`${data.totals.unread_conversations} unread conversations`} />
            </div>

            <SectionCard title="Messages per day">
              <DailyChart daily={data.daily} />
            </SectionCard>

            <div className="grid gap-6 lg:grid-cols-2">
              <SectionCard title="By channel">
                {data.by_platform.length === 0 ? (
                  <p className="text-sm text-muted">No messages in this period.</p>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="text-left text-muted">
                          <th className="pb-2 font-medium">Channel</th>
                          <th className="pb-2 text-right font-medium">Customers</th>
                          <th className="pb-2 text-right font-medium">Bot</th>
                          <th className="pb-2 text-right font-medium">Agents</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-border">
                        {data.by_platform.map((p) => (
                          <tr key={p.platform}>
                            <td className="py-2">{PLATFORM_LABEL[p.platform as InboxPlatform] ?? human(p.platform)}</td>
                            <td className="py-2 text-right tabular-nums">{p.customer}</td>
                            <td className="py-2 text-right tabular-nums">{p.bot}</td>
                            <td className="py-2 text-right tabular-nums">{p.agent}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </SectionCard>

              <SectionCard title="Comments" description={`${data.totals.unread_comments} unread overall`}>
                <div className="grid gap-6 sm:grid-cols-2">
                  <div>
                    <h3 className="mb-3 text-sm font-medium">Tone</h3>
                    <Breakdown
                      rows={data.comments.by_classification.map((r) => ({ label: human(r.classification), n: r.n }))}
                      empty="No comments in this period."
                    />
                  </div>
                  <div>
                    <h3 className="mb-3 text-sm font-medium">Bot action</h3>
                    <Breakdown rows={data.comments.by_action.map((r) => ({ label: human(r.action), n: r.n }))} empty="No comments in this period." />
                  </div>
                </div>
              </SectionCard>
            </div>
          </>
        )
      )}
    </div>
  );
}
