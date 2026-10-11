"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { InboxView } from "@/components/inbox";
import { isInboxPlatform } from "@/lib/inbox-api";

export default function InboxPage() {
  const { platform } = useParams<{ platform: string }>();

  if (!isInboxPlatform(platform)) {
    return (
      <div className="mx-auto max-w-md space-y-3 py-16 text-center">
        <h1 className="text-xl font-semibold">Inbox not found</h1>
        <p className="text-sm text-muted">Choose WhatsApp, Facebook or Instagram from the sidebar.</p>
        <Link href="/dashboard" className="text-sm text-brand hover:underline">
          Back to overview
        </Link>
      </div>
    );
  }

  return <InboxView platform={platform} />;
}
