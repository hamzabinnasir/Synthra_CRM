"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";
import { Alert, Button, Input, Spinner } from "@/components/ui";
import { ApiError, authApi } from "@/lib/api";

const MIN_PASSWORD = 10; // mirrors the backend policy (security/passwords.py)

function ResetPassword() {
  const token = useSearchParams().get("token");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  if (!token) {
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-semibold">Reset link problem</h1>
        <Alert tone="error">This link is missing its token. Request a new reset email.</Alert>
        <Link href="/forgot-password" className="block text-center text-sm font-medium text-brand hover:underline">
          Request a new link
        </Link>
      </div>
    );
  }

  const tooShort = password.length > 0 && password.length < MIN_PASSWORD;
  const mismatch = confirm.length > 0 && confirm !== password;

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!token || tooShort || mismatch) return;
    setBusy(true);
    setError(null);
    try {
      const res = await authApi.resetPassword(token, password);
      setDone(res.message);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not update the password.");
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-semibold">Password updated</h1>
        <Alert tone="success">{done} All other devices were signed out.</Alert>
        <Link href="/login" className="block text-center text-sm font-medium text-brand hover:underline">
          Go to sign in
        </Link>
      </div>
    );
  }

  return (
    <>
      <h1 className="text-xl font-semibold">Choose a new password</h1>
      <form onSubmit={onSubmit} className="mt-6 space-y-4" noValidate>
        {error && <Alert tone="error">{error}</Alert>}
        <Input
          label="New password"
          type="password"
          autoComplete="new-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          error={tooShort ? `Use at least ${MIN_PASSWORD} characters.` : undefined}
          hint={`At least ${MIN_PASSWORD} characters.`}
        />
        <Input
          label="Confirm new password"
          type="password"
          autoComplete="new-password"
          required
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          error={mismatch ? "Passwords do not match." : undefined}
        />
        <Button type="submit" full loading={busy} disabled={!password || tooShort || mismatch || !confirm}>
          Update password
        </Button>
      </form>
    </>
  );
}

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={<Spinner />}>
      <ResetPassword />
    </Suspense>
  );
}
