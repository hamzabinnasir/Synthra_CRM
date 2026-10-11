"use client";

import Link from "next/link";
import { Card } from "@/components/ui";
import { useAuth } from "@/lib/auth-context";

const STEPS = [
  {
    title: "Connect your database",
    body: "Bring your own Supabase or Postgres. Synthra creates every table, index and policy for you. Your customer data never leaves your database.",
    stage: "Available",
    href: "/settings/database",
  },
  {
    title: "Connect a channel",
    body: "Set up only what you need: WhatsApp, Facebook or Instagram. Each channel has its own page with the exact webhook URL and verify token for Meta.",
    stage: "Available",
    href: "/settings/channels",
  },
  {
    title: "Teach your bot",
    body: "Upload documents to the knowledge base and set the tone, system prompt and LLM provider per channel.",
    stage: "Coming soon",
    href: null,
  },
  {
    title: "Handle conversations",
    body: "Read and reply to messages, hand over from the bot to a human, and moderate public comments from one place.",
    stage: "Coming soon",
    href: null,
  },
] as const;

export default function DashboardPage() {
  const { me } = useAuth();
  const name = me?.user.display_name?.split(" ")[0] || "there";

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Welcome, {name} 👋</h1>
        <p className="mt-1 text-sm text-muted">
          Let’s get <strong className="font-medium text-foreground">{me?.tenant.name}</strong> ready. You only configure the
          channels you actually use.
        </p>
      </div>

      <ol className="grid gap-4 sm:grid-cols-2">
        {STEPS.map((step, i) => (
          <li key={step.title}>
            <Card className="h-full p-5">
              <div className="flex items-start gap-4">
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-soft text-sm font-semibold text-brand">
                  {i + 1}
                </span>
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="font-semibold">
                      {step.href ? (
                        <Link href={step.href} className="hover:text-brand hover:underline">
                          {step.title}
                        </Link>
                      ) : (
                        step.title
                      )}
                    </h2>
                    <span className="rounded-full bg-surface-2 px-2 py-0.5 text-[11px] font-medium text-muted">{step.stage}</span>
                  </div>
                  <p className="mt-1.5 text-sm leading-relaxed text-muted">{step.body}</p>
                </div>
              </div>
            </Card>
          </li>
        ))}
      </ol>
    </div>
  );
}
