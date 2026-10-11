"use client";

import { Button } from "@/components/ui";

export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-3 px-4 text-center">
      <h1 className="text-2xl font-semibold">Something went wrong</h1>
      <p className="max-w-sm text-sm text-muted">An unexpected error occurred. You can try again.</p>
      {error.digest && <p className="text-xs text-muted">Reference: {error.digest}</p>}
      <Button onClick={reset} className="mt-2">
        Try again
      </Button>
    </main>
  );
}
