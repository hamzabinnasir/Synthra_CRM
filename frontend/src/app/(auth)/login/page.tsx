"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Alert, Button, FullPageSpinner, Input } from "@/components/ui";
import { ApiError, authApi } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useRedirectIfAuthenticated } from "@/lib/guards";

export default function LoginPage() {
  const pending = useRedirectIfAuthenticated();
  const { login } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  if (pending) return <FullPageSpinner />;

  const needsVerification = !!error && /verif/i.test(`${error.code} ${error.message}`);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await login(email.trim(), password);
      router.replace("/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(0, "unknown", "Could not sign in."));
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    setNotice(null);
    try {
      const res = await authApi.resendVerification(email.trim());
      setNotice(res.message);
    } catch (err) {
      setError(err instanceof ApiError ? err : null);
    }
  }

  return (
    <>
      <h1 className="text-xl font-semibold">Welcome back</h1>
      <p className="mt-1 text-sm text-muted">Sign in to manage your chatbots and inboxes.</p>

      <form onSubmit={onSubmit} className="mt-6 space-y-4" noValidate>
        {error && (
          <Alert tone="error">
            {error.message}
            {needsVerification && email && (
              <button type="button" onClick={resend} className="ml-2 font-medium underline">
                Resend verification email
              </button>
            )}
          </Alert>
        )}
        {notice && <Alert tone="success">{notice}</Alert>}

        <Input
          label="Email"
          type="email"
          autoComplete="email"
          inputMode="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@company.com"
        />
        <Input
          label="Password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />

        <div className="flex justify-end">
          <Link href="/forgot-password" className="text-sm font-medium text-brand hover:underline">
            Forgot password?
          </Link>
        </div>

        <Button type="submit" full loading={busy} disabled={!email || !password}>
          Sign in
        </Button>
      </form>

      <p className="mt-6 text-center text-sm text-muted">
        New to Synthra?{" "}
        <Link href="/register" className="font-medium text-brand hover:underline">
          Create an account
        </Link>
      </p>
    </>
  );
}
