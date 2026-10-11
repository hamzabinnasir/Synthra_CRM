"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { Alert, Spinner } from "@/components/ui";
import { ApiError, authApi } from "@/lib/api";

type State = { kind: "working" } | { kind: "ok"; message: string } | { kind: "error"; message: string };

function VerifyEmail() {
  const token = useSearchParams().get("token");
  const [state, setState] = useState<State>(token ? { kind: "working" } : { kind: "error", message: "This link is missing its token." });
  const started = useRef(false); // verification tokens are single-use: never submit twice (StrictMode)

  useEffect(() => {
    if (!token || started.current) return;
    started.current = true;
    authApi
      .verifyEmail(token)
      .then((res) => setState({ kind: "ok", message: res.message }))
      .catch((err) =>
        setState({ kind: "error", message: err instanceof ApiError ? err.message : "Could not verify your email." }),
      );
  }, [token]);

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Email verification</h1>
      {state.kind === "working" && (
        <p className="flex items-center gap-2 text-sm text-muted" role="status">
          <Spinner /> Verifying your email…
        </p>
      )}
      {state.kind === "ok" && <Alert tone="success">{state.message}</Alert>}
      {state.kind === "error" && (
        <>
          <Alert tone="error">{state.message}</Alert>
          <p className="text-sm text-muted">
            The link may have expired. Sign in and choose “Resend verification email” to get a new one.
          </p>
        </>
      )}
      <Link href="/login" className="block text-center text-sm font-medium text-brand hover:underline">
        Go to sign in
      </Link>
    </div>
  );
}

export default function VerifyEmailPage() {
  return (
    <Suspense fallback={<Spinner />}>
      <VerifyEmail />
    </Suspense>
  );
}
