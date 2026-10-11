"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { CommentsView } from "@/components/comments";
import { isCommentPlatform } from "@/lib/comments-api";

export default function CommentsPage() {
  const { platform } = useParams<{ platform: string }>();

  if (!isCommentPlatform(platform)) {
    return (
      <div className="mx-auto max-w-md space-y-3 py-16 text-center">
        <h1 className="text-xl font-semibold">Comments not found</h1>
        <p className="text-sm text-muted">Choose Facebook or Instagram comments from the sidebar.</p>
        <Link href="/dashboard" className="text-sm text-brand hover:underline">
          Back to overview
        </Link>
      </div>
    );
  }

  return <CommentsView platform={platform} />;
}
