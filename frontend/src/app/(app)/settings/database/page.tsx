"use client";

import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Card, Input } from "@/components/ui";
import { Badge, PageHeader, SectionCard, Skeleton, type BadgeTone } from "@/components/form";
import {
  databaseApi,
  errorText,
  type DbInfo,
  type DbTestResult,
  type MigrationRow,
  type PendingMigration,
} from "@/lib/settings-api";

function statusBadge(info: DbInfo): { tone: BadgeTone; label: string } {
  if (!info.connected) return { tone: "neutral", label: "Not connected" };
  if (info.status === "migrated") return { tone: info.up_to_date ? "success" : "warning", label: info.up_to_date ? "Ready" : "Update available" };
  if (info.status === "error") return { tone: "danger", label: "Needs attention" };
  return { tone: "info", label: "Connected, setup pending" };
}

export default function DatabaseSettingsPage() {
  const [info, setInfo] = useState<DbInfo | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [migrations, setMigrations] = useState<MigrationRow[] | null>(null);

  const [url, setUrl] = useState("");
  const [testResult, setTestResult] = useState<DbTestResult | null>(null);
  const [preview, setPreview] = useState<PendingMigration[] | null>(null);
  const [busy, setBusy] = useState<"test" | "save" | "preview" | "migrate" | "check" | "sql" | "disconnect" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadMigrations = useCallback(async () => {
    try {
      setMigrations((await databaseApi.migrations()).migrations);
    } catch {
      setMigrations(null); // shown as "unavailable"; the main error banner covers real problems
    }
  }, []);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const next = await databaseApi.info();
      setInfo(next);
      if (next.connected) void loadMigrations();
      else setMigrations(null);
    } catch (e) {
      setLoadError(errorText(e));
    }
  }, [loadMigrations]);

  useEffect(() => {
    void load();
  }, [load]);

  async function run<T>(name: NonNullable<typeof busy>, fn: () => Promise<T>, onDone: (r: T) => void | Promise<void>) {
    setBusy(name);
    setError(null);
    setNotice(null);
    try {
      await onDone(await fn());
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  const testConnection = () =>
    run("test", () => databaseApi.test(url.trim()), (r) => {
      setTestResult(r);
    });

  const saveConnection = () =>
    run("save", () => databaseApi.save(url.trim()), async () => {
      setUrl(""); // the connection string is never kept in the browser after saving
      setTestResult(null);
      setPreview(null);
      setNotice("Connection saved and encrypted. Next, preview and apply the setup.");
      await load();
    });

  const previewChanges = () =>
    run("preview", () => databaseApi.preview(), (r) => {
      setPreview(r.pending);
      if (r.pending.length === 0) setNotice("Nothing to apply. Your database is already up to date.");
    });

  const applySetup = () =>
    run("migrate", () => databaseApi.migrate(), async (r) => {
      setPreview(null);
      setNotice(r.applied.length ? `Applied ${r.applied.length} migration${r.applied.length === 1 ? "" : "s"}.` : "Already up to date.");
      await load();
    });

  const recheck = () =>
    run("check", () => databaseApi.check(), async (r) => {
      setMigrations(r.migrations);
      setNotice("Checked. Status updated.");
      await load();
    });

  const downloadSql = () =>
    run("sql", () => databaseApi.manualSql(), (sql) => {
      const href = URL.createObjectURL(new Blob([sql], { type: "text/plain" }));
      const a = document.createElement("a");
      a.href = href;
      a.download = "synthra-crm-setup.sql";
      a.click();
      URL.revokeObjectURL(href);
    });

  const disconnect = () => {
    if (!window.confirm("Forget this database connection? Your data stays in your database, but the bot and dashboards will stop working until you reconnect.")) return;
    return run("disconnect", () => databaseApi.disconnect(), async () => {
      setPreview(null);
      setNotice("Disconnected. Your data in the database was not touched.");
      await load();
    });
  };

  if (loadError) {
    return (
      <div className="space-y-4">
        <PageHeader title="Database" />
        <Alert tone="error">{loadError}</Alert>
        <Button variant="secondary" onClick={() => void load()}>
          Try again
        </Button>
      </div>
    );
  }

  if (!info) {
    return (
      <div className="space-y-6" role="status" aria-label="Loading database settings">
        <Skeleton className="h-9 w-48" />
        <Skeleton className="h-40" />
        <Skeleton className="h-56" />
      </div>
    );
  }

  const badge = statusBadge(info);
  const pendingCount = migrations ? migrations.filter((m) => m.state !== "applied").length : null;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Database"
        description="Your conversations, comments and knowledge base live in your own database. Synthra connects to it and creates everything it needs."
        actions={<Badge tone={badge.tone}>{badge.label}</Badge>}
      />

      {error && <Alert tone="error">{error}</Alert>}
      {notice && <Alert tone="success">{notice}</Alert>}

      <SectionCard
        title={info.connected ? "Replace connection" : "Connect your database"}
        description="Supabase or any PostgreSQL 14+ database with the pgvector extension."
      >
        <Alert tone="info">
          <p className="font-medium">Why a connection string, not the Supabase API keys?</p>
          <p className="mt-1 leading-relaxed">
            Supabase anon and service keys can read and write rows but cannot create tables. Setup needs the Postgres connection string
            (Supabase: Project settings, Database, Connection string, URI). It is used to create the schema and by your bot to read and write
            data. It is encrypted at rest and never shown again after you save it.
          </p>
        </Alert>

        <Input
          label="Postgres connection string"
          type="password"
          autoComplete="off"
          spellCheck={false}
          placeholder="postgresql://postgres:password@db.xxxx.supabase.co:5432/postgres"
          hint="Use the 'postgres' role so setup can create tables. Replace [YOUR-PASSWORD] with your database password."
          value={url}
          onChange={(e) => {
            setUrl(e.target.value);
            setTestResult(null);
          }}
        />

        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onClick={testConnection} loading={busy === "test"} disabled={url.trim().length < 10 || busy !== null}>
            Test connection
          </Button>
          <Button onClick={saveConnection} loading={busy === "save"} disabled={url.trim().length < 10 || busy !== null || (testResult !== null && !testResult.ok)}>
            Save connection
          </Button>
        </div>

        {testResult && (
          <div className="space-y-3 rounded-lg border border-border bg-surface-2 p-4 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={testResult.ok ? "success" : "danger"}>{testResult.ok ? "Connection works" : "Not ready"}</Badge>
              <span className="text-muted">
                {testResult.host_hint} · PostgreSQL {testResult.server_version || "?"}
              </span>
            </div>
            <ul className="space-y-1">
              <li>{testResult.can_create_schema ? "✓" : "✗"} Can create tables (user: {testResult.user || "?"})</li>
              <li>
                {testResult.vector_installed ? "✓ pgvector installed" : testResult.vector_available ? "✓ pgvector available (setup will enable it)" : "✗ pgvector not available"}
              </li>
              <li>
                {testResult.pending_migrations === 0 ? "✓ Schema is up to date" : `${testResult.pending_migrations} setup step${testResult.pending_migrations === 1 ? "" : "s"} will be applied`}
              </li>
            </ul>
            {testResult.problems.map((p) => (
              <Alert key={p} tone="error">
                {p}
              </Alert>
            ))}
            {testResult.warnings.map((w) => (
              <Alert key={w} tone="warning">
                {w}
              </Alert>
            ))}
          </div>
        )}
      </SectionCard>

      {info.connected && (
        <SectionCard title="Schema setup" description="Versioned, idempotent migrations. Safe to run again at any time.">
          <dl className="grid gap-4 text-sm sm:grid-cols-3">
            <div>
              <dt className="text-muted">Database</dt>
              <dd className="mt-0.5 font-medium">{info.host_hint}</dd>
            </div>
            <div>
              <dt className="text-muted">Schema version</dt>
              <dd className="mt-0.5 font-medium">
                {info.schema_version ?? "not applied"} <span className="font-normal text-muted">/ latest {info.latest_version ?? "n/a"}</span>
              </dd>
            </div>
            <div>
              <dt className="text-muted">Last checked</dt>
              <dd className="mt-0.5 font-medium">{info.last_checked_at ? new Date(info.last_checked_at).toLocaleString() : "never"}</dd>
            </div>
          </dl>

          {info.last_error && <Alert tone="error">{info.last_error}</Alert>}

          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={previewChanges} loading={busy === "preview"} disabled={busy !== null}>
              Preview changes (dry run)
            </Button>
            <Button onClick={applySetup} loading={busy === "migrate"} disabled={busy !== null || info.up_to_date === true}>
              {info.schema_version ? "Apply updates" : "Set up database"}
            </Button>
            <Button variant="ghost" onClick={recheck} loading={busy === "check"} disabled={busy !== null}>
              Re-check
            </Button>
          </div>

          {preview && preview.length > 0 && (
            <div className="space-y-2">
              <p className="text-sm font-medium">These steps would run (nothing has been changed yet):</p>
              {preview.map((m) => (
                <details key={m.version} className="rounded-lg border border-border bg-surface-2 px-4 py-2.5 text-sm">
                  <summary className="cursor-pointer font-medium">
                    {m.version} · {m.name}
                  </summary>
                  <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap text-xs leading-relaxed text-muted">{m.sql}</pre>
                </details>
              ))}
            </div>
          )}

          <div>
            <h3 className="mb-2 text-sm font-semibold">Migration status</h3>
            {migrations === null ? (
              <p className="text-sm text-muted">Status unavailable. Check the connection and press Re-check.</p>
            ) : (
              <div className="overflow-x-auto rounded-lg border border-border">
                <table className="w-full text-left text-sm">
                  <thead className="bg-surface-2 text-xs uppercase tracking-wide text-muted">
                    <tr>
                      <th className="px-4 py-2 font-medium">Version</th>
                      <th className="px-4 py-2 font-medium">Name</th>
                      <th className="px-4 py-2 font-medium">State</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {migrations.map((m) => (
                      <tr key={m.version}>
                        <td className="px-4 py-2 font-mono text-xs">{m.version}</td>
                        <td className="px-4 py-2">{m.name}</td>
                        <td className="px-4 py-2">
                          <Badge tone={m.state === "applied" ? "success" : m.state === "pending" ? "warning" : "danger"}>{m.state}</Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {pendingCount !== null && pendingCount > 0 && <p className="mt-2 text-xs text-muted">{pendingCount} step(s) still to apply.</p>}
          </div>
        </SectionCard>
      )}

      <Card className="p-5">
        <h2 className="font-semibold">Automatic setup did not work?</h2>
        <p className="mt-1 text-sm leading-relaxed text-muted">
          Download the full setup script, paste it into your Supabase SQL editor and run it, then come back and press Re-check. The script
          contains no secrets.
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button variant="secondary" onClick={downloadSql} loading={busy === "sql"} disabled={busy !== null}>
            Download setup SQL
          </Button>
          {info.connected && (
            <Button variant="danger" onClick={disconnect} loading={busy === "disconnect"} disabled={busy !== null}>
              Disconnect database
            </Button>
          )}
        </div>
      </Card>
    </div>
  );
}
