"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Button, Input } from "@/components/ui";
import { Badge, CopyField, PageHeader, SectionCard, Select, Skeleton, Textarea, Toggle } from "@/components/form";
import { channelStatus } from "@/lib/channel-status";
import { ApiError } from "@/lib/api";
import {
  channelsApi,
  DOCS_URL,
  errorText,
  LLM_PROVIDERS,
  MODERATION_ACTIONS,
  TONES,
  type Behaviour,
  type ChannelTestResult,
  type ChannelView,
} from "@/lib/settings-api";

const BEHAVIOUR_KEYS = [
  "llm_provider",
  "llm_model",
  "system_prompt",
  "tone",
  "spam_action",
  "abuse_action",
  "business_phone",
  "pause_on_human",
  "handoff_message",
  "fallback_message",
] as const;

/** Where to paste the webhook, per platform. */
const WEBHOOK_STEPS: Record<ChannelView["platform"], string> = {
  whatsapp: "Meta for Developers > your app > WhatsApp > Configuration > Webhook > Edit.",
  facebook: "Meta for Developers > your app > Messenger or Webhooks > Page > Edit subscription.",
  instagram: "Meta for Developers > your app > Instagram > API setup with Instagram login > Configure webhooks.",
};

function initialInputs(c: ChannelView): Record<string, string> {
  const out: Record<string, string> = {};
  for (const f of c.fields) out[f.key] = f.secret ? "" : (c.values[f.key]?.value ?? "");
  return out;
}

export default function ChannelSettingsPage() {
  const { id } = useParams<{ id: string }>();

  const [ch, setCh] = useState<ChannelView | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [behaviour, setBehaviour] = useState<Behaviour>({});
  const [enabled, setEnabled] = useState(false);
  const [botEnabled, setBotEnabled] = useState(false);

  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [test, setTest] = useState<ChannelTestResult | null>(null);
  const [busy, setBusy] = useState<"save" | "test" | "rotate" | "clear" | null>(null);

  const adopt = useCallback((c: ChannelView) => {
    setCh(c);
    setInputs(initialInputs(c));
    setBehaviour(c.behaviour);
    setEnabled(c.enabled);
    setBotEnabled(c.bot_enabled);
  }, []);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      adopt(await channelsApi.get(id));
    } catch (e) {
      setLoadError(e instanceof ApiError && e.status === 404 ? "This channel does not exist." : errorText(e));
    }
  }, [id, adopt]);

  useEffect(() => {
    setCh(null);
    setTest(null);
    setError(null);
    setNotice(null);
    setFieldErrors({});
    void load();
  }, [load]);

  const credentialFields = useMemo(() => ch?.fields.filter((f) => !f.key.startsWith("llm_")) ?? [], [ch]);
  const llmKeyField = useMemo(() => ch?.fields.find((f) => f.key === "llm_api_key"), [ch]);

  /** Only what the user actually changed is sent. Blank secret inputs mean "keep the saved one". */
  const changedValues = useMemo(() => {
    const out: Record<string, string> = {};
    if (!ch) return out;
    for (const f of ch.fields) {
      const typed = (inputs[f.key] ?? "").trim();
      if (f.secret ? typed !== "" : typed !== (ch.values[f.key]?.value ?? "") && typed !== "") out[f.key] = typed;
    }
    return out;
  }, [ch, inputs]);

  const dirty = useMemo(() => {
    if (!ch) return false;
    const behaviourChanged = BEHAVIOUR_KEYS.some((k) => (behaviour[k] ?? "") !== (ch.behaviour[k] ?? ""));
    return Object.keys(changedValues).length > 0 || behaviourChanged || enabled !== ch.enabled || botEnabled !== ch.bot_enabled;
  }, [ch, changedValues, behaviour, enabled, botEnabled]);

  const setB = (key: string, value: string) => setBehaviour((b) => ({ ...b, [key]: value }));

  async function save() {
    if (!ch) return;
    setBusy("save");
    setError(null);
    setNotice(null);
    setFieldErrors({});
    try {
      const payload: Behaviour = {};
      for (const k of BEHAVIOUR_KEYS) payload[k] = behaviour[k] ?? "";
      const saved = await channelsApi.save(ch.id, { values: changedValues, behaviour: payload, enabled, bot_enabled: botEnabled });
      adopt(saved);
      setTest(null);
      setNotice(
        saved.missing.length === 0 ? "Saved. Press Test connection to confirm it works." : "Saved. Some required settings are still empty.",
      );
    } catch (e) {
      if (e instanceof ApiError && Object.keys(e.fieldErrors).length > 0) {
        setFieldErrors(e.fieldErrors);
        setError("Some settings need attention. Check the highlighted fields.");
      } else {
        setError(errorText(e));
      }
    } finally {
      setBusy(null);
    }
  }

  async function runTest() {
    if (!ch) return;
    setBusy("test");
    setError(null);
    setNotice(null);
    try {
      setTest(await channelsApi.test(ch.id));
      adopt({ ...(await channelsApi.get(ch.id)) }); // pick up the new status / last error
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  async function rotate() {
    if (!ch) return;
    const shared = ch.webhook.shared_with.length > 0 ? ` The token is shared with ${ch.webhook.shared_with.join(" and ")}, so it changes there too.` : "";
    if (!window.confirm(`Generate a new verify token? You must paste the new token into Meta, otherwise Meta cannot re-verify your webhook.${shared}`)) return;
    setBusy("rotate");
    setError(null);
    try {
      const next = await channelsApi.rotateVerifyToken(ch.id);
      setCh((cur) => (cur ? { ...cur, webhook: next.webhook } : cur));
      setNotice("New verify token generated. Paste it into Meta.");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  async function clearChannel() {
    if (!ch) return;
    if (!window.confirm(`Remove all saved settings and credentials for ${ch.title}? The bot will stop answering on this channel.`)) return;
    setBusy("clear");
    setError(null);
    try {
      await channelsApi.clear(ch.id);
      setTest(null);
      setNotice("All settings for this channel were removed.");
      await load();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  if (loadError) {
    return (
      <div className="space-y-4">
        <Link href="/settings/channels" className="text-sm text-brand hover:underline">
          ← All channels
        </Link>
        <Alert tone="error">{loadError}</Alert>
        <Button variant="secondary" onClick={() => void load()}>
          Try again
        </Button>
      </div>
    );
  }

  if (!ch) {
    return (
      <div className="space-y-6" role="status" aria-label="Loading channel settings">
        <Skeleton className="h-9 w-64" />
        <Skeleton className="h-64" />
        <Skeleton className="h-48" />
      </div>
    );
  }

  const status = channelStatus({ status: ch.status, enabled: ch.enabled });
  const isInbox = ch.kind === "inbox";
  const guideHref = DOCS_URL ? `${DOCS_URL}${ch.guide.replace(/^\/docs/, "")}` : null;
  const canTest = !dirty && ch.missing.length === 0;

  return (
    <div className="space-y-6">
      <Link href="/settings/channels" className="text-sm text-brand hover:underline">
        ← All channels
      </Link>

      <PageHeader title={ch.title} description={ch.summary} actions={<Badge tone={status.tone}>{status.label}</Badge>} />

      {error && <Alert tone="error">{error}</Alert>}
      {notice && <Alert tone="success">{notice}</Alert>}
      {ch.status === "error" && ch.last_error && <Alert tone="error">Last connection test failed: {ch.last_error}</Alert>}

      <SectionCard title="Status" description="Turn this channel on once it is configured and tested.">
        <Toggle
          label="Channel enabled"
          description="When off, incoming webhooks for this channel are rejected."
          checked={enabled}
          onChange={setEnabled}
          error={fieldErrors.enabled}
        />
        <Toggle
          label="Bot enabled"
          description={isInbox ? "The bot answers new messages. You can still pause it for individual customers in the inbox." : "The bot classifies and replies to comments."}
          checked={botEnabled}
          onChange={setBotEnabled}
        />
      </SectionCard>

      <SectionCard
        title="Connection credentials"
        description={
          <>
            From your Meta app.{" "}
            {guideHref ? (
              <a href={guideHref} target="_blank" rel="noreferrer" className="text-brand hover:underline">
                Open the step-by-step guide
              </a>
            ) : (
              <>
                Step-by-step guide: <code className="text-xs">{ch.guide.replace(/^\//, "")}</code>
              </>
            )}
          </>
        }
      >
        {credentialFields.map((f) => {
          const saved = ch.values[f.key];
          return (
            <Input
              key={f.key}
              label={f.required ? `${f.label} *` : f.label}
              type={f.secret ? "password" : "text"}
              autoComplete="off"
              spellCheck={false}
              placeholder={f.secret && saved?.saved ? saved.masked : f.placeholder}
              hint={
                <>
                  {f.help}
                  {f.secret && saved?.saved ? " A value is saved. Leave this empty to keep it." : ""}
                </>
              }
              value={inputs[f.key] ?? ""}
              error={fieldErrors[f.key]}
              onChange={(e) => setInputs((s) => ({ ...s, [f.key]: e.target.value }))}
            />
          );
        })}
      </SectionCard>

      <SectionCard title="Webhook" description="Paste these two values into Meta so it can send events to Synthra.">
        {ch.webhook.shared_with.length > 0 && (
          <p className="rounded-md bg-surface-2 px-3 py-2 text-sm text-muted">
            This is the single {ch.platform[0].toUpperCase() + ch.platform.slice(1)} webhook. Set it up once in Meta; it also delivers events for{" "}
            {ch.webhook.shared_with.join(" and ")}, so you do not need a second webhook.
          </p>
        )}
        <ol className="list-decimal space-y-1 pl-5 text-sm text-muted">
          <li>{WEBHOOK_STEPS[ch.platform]}</li>
          <li>Paste the callback URL and verify token below, then save in Meta.</li>
          <li>
            Subscribe to: {ch.webhook.subscribe_to.map((s) => (
              <code key={s} className="mr-1 rounded bg-surface-2 px-1.5 py-0.5 text-xs text-foreground">
                {s}
              </code>
            ))}
          </li>
        </ol>
        <CopyField label="Callback URL" value={ch.webhook.url} hint={`One URL for all ${ch.platform[0].toUpperCase() + ch.platform.slice(1)} channels in your workspace.`} />
        <CopyField label="Verify token" value={ch.webhook.verify_token} concealed hint="Meta sends this back to prove the URL is yours. The same token is used by every channel on this webhook." />
        <div>
          <Button variant="secondary" onClick={rotate} loading={busy === "rotate"} disabled={busy !== null}>
            Generate new verify token
          </Button>
        </div>
      </SectionCard>

      <SectionCard title="Bot behaviour" description="How the bot thinks and talks on this channel. Only the provider you choose is used.">
        <div className="grid gap-5 sm:grid-cols-2">
          <Select
            label="LLM provider"
            options={LLM_PROVIDERS}
            value={behaviour.llm_provider ?? "anthropic"}
            error={fieldErrors.llm_provider}
            onChange={(e) => setB("llm_provider", e.target.value)}
          />
          <Input
            label="Model (optional)"
            placeholder="Leave empty for the provider default"
            value={behaviour.llm_model ?? ""}
            error={fieldErrors.llm_model}
            onChange={(e) => setB("llm_model", e.target.value)}
          />
        </div>

        {llmKeyField && (
          <Input
            label="LLM API key"
            type="password"
            autoComplete="off"
            spellCheck={false}
            placeholder={ch.values.llm_api_key?.saved ? ch.values.llm_api_key.masked : llmKeyField.placeholder}
            hint={
              <>
                {llmKeyField.help}
                {ch.values.llm_api_key?.saved ? " A key is saved. Leave empty to keep it." : ""} Billed to your own account with the provider.
              </>
            }
            value={inputs.llm_api_key ?? ""}
            error={fieldErrors.llm_api_key}
            onChange={(e) => setInputs((s) => ({ ...s, llm_api_key: e.target.value }))}
          />
        )}

        <Textarea
          label="System prompt"
          placeholder="Describe your business, what the bot may answer, and what it must never do."
          hint={`${(behaviour.system_prompt ?? "").length} / 8000 characters`}
          value={behaviour.system_prompt ?? ""}
          error={fieldErrors.system_prompt}
          onChange={(e) => setB("system_prompt", e.target.value)}
        />

        <div className="grid gap-5 sm:grid-cols-3">
          <Select
            label="Tone"
            options={TONES.map((t) => ({ value: t, label: t[0].toUpperCase() + t.slice(1) }))}
            value={behaviour.tone ?? "friendly"}
            error={fieldErrors.tone}
            onChange={(e) => setB("tone", e.target.value)}
          />
          <Select
            label="When something is spam"
            options={MODERATION_ACTIONS}
            value={behaviour.spam_action ?? "none"}
            error={fieldErrors.spam_action}
            onChange={(e) => setB("spam_action", e.target.value)}
          />
          <Select
            label="When something is abusive"
            options={MODERATION_ACTIONS}
            value={behaviour.abuse_action ?? "none"}
            error={fieldErrors.abuse_action}
            onChange={(e) => setB("abuse_action", e.target.value)}
          />
        </div>

        {isInbox && (
          <>
            <Toggle
              label="Pause the bot when a person replies"
              description="After a team member sends a message, the bot stays quiet in that conversation until you resume it."
              checked={(behaviour.pause_on_human ?? "true") === "true"}
              onChange={(v) => setB("pause_on_human", v ? "true" : "false")}
              error={fieldErrors.pause_on_human}
            />
            <Textarea
              label="Handoff message"
              className="min-h-20"
              placeholder="Shown to the customer when the bot passes the chat to a person."
              hint={`${(behaviour.handoff_message ?? "").length} / 500`}
              value={behaviour.handoff_message ?? ""}
              error={fieldErrors.handoff_message}
              onChange={(e) => setB("handoff_message", e.target.value)}
            />
          </>
        )}
        <Textarea
          label="Fallback message"
          className="min-h-20"
          placeholder="Sent when the bot cannot answer, for example if the AI provider is unavailable."
          hint={`${(behaviour.fallback_message ?? "").length} / 500`}
          value={behaviour.fallback_message ?? ""}
          error={fieldErrors.fallback_message}
          onChange={(e) => setB("fallback_message", e.target.value)}
        />
        {ch.platform === "whatsapp" && (
          <Input
            label="Business phone (optional)"
            placeholder="+92 300 1234567"
            hint="Shown to customers when the bot hands over to a person."
            value={behaviour.business_phone ?? ""}
            error={fieldErrors.business_phone}
            onChange={(e) => setB("business_phone", e.target.value)}
          />
        )}
      </SectionCard>

      {test && (
        <Alert tone={test.ok ? "success" : "error"}>
          <p className="font-medium">{test.ok ? "Connection works" : "Connection failed"}</p>
          <p className="mt-1">{test.message}</p>
          {Object.keys(test.details ?? {}).length > 0 && (
            <ul className="mt-2 space-y-0.5 text-xs">
              {Object.entries(test.details).map(([k, v]) => (
                <li key={k}>
                  {k.replace(/_/g, " ")}: {String(v)}
                </li>
              ))}
            </ul>
          )}
          {test.warnings?.map((w) => (
            <p key={w} className="mt-2 text-xs">
              ⚠ {w}
            </p>
          ))}
        </Alert>
      )}

      <div className="sticky bottom-0 -mx-1 flex flex-wrap items-center gap-2 border-t border-border bg-background/90 px-1 py-4 backdrop-blur">
        <Button onClick={save} loading={busy === "save"} disabled={!dirty || busy !== null}>
          Save changes
        </Button>
        <Button variant="secondary" onClick={runTest} loading={busy === "test"} disabled={!canTest || busy !== null}>
          Test connection
        </Button>
        {dirty ? (
          <span className="text-xs text-muted">Unsaved changes. Save before testing.</span>
        ) : ch.missing.length > 0 ? (
          <span className="text-xs text-muted">Fill in the required fields (*) first.</span>
        ) : null}
        <Button variant="ghost" className="ml-auto text-danger" onClick={clearChannel} loading={busy === "clear"} disabled={busy !== null}>
          Remove settings
        </Button>
      </div>
    </div>
  );
}
