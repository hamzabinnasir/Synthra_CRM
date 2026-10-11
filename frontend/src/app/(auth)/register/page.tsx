"use client";

import Link from "next/link";
import { useState, type ChangeEvent, type FormEvent } from "react";
import { Alert, Button, FullPageSpinner, Input } from "@/components/ui";
import { API_BASE, ApiError, authApi } from "@/lib/api";
import { useRedirectIfAuthenticated } from "@/lib/guards";

const MIN_PASSWORD = 10; // mirrors the backend policy (security/passwords.py)

export default function RegisterPage() {
  const pending = useRedirectIfAuthenticated();
  const [form, setForm] = useState({ display_name: "", workspace_name: "", email: "", password: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  if (pending) return <FullPageSpinner />;

  const set = (key: keyof typeof form) => (e: ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));

  const passwordError =
    form.password.length > 0 && form.password.length < MIN_PASSWORD ? `Use at least ${MIN_PASSWORD} characters.` : undefined;

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (passwordError) return;
    setBusy(true);
    setError(null);
    try {
      await authApi.register({
        email: form.email.trim(),
        password: form.password,
        display_name: form.display_name.trim(),
        workspace_name: form.workspace_name.trim(),
      });
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create the account.");
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-semibold">Check your email</h1>
        <Alert tone="success">
          If <strong>{form.email}</strong> can be used, a verification link is on its way. It expires in 24 hours.
        </Alert>
        {API_BASE.includes("localhost") && (
          <p className="text-xs text-muted">
            Running locally without SMTP? The verification link is printed in the backend server log.
          </p>
        )}
        <Link href="/login" className="block text-center text-sm font-medium text-brand hover:underline">
          Back to sign in
        </Link>
      </div>
    );
  }

  const ready = form.workspace_name && form.email && form.password.length >= MIN_PASSWORD;

  return (
    <>
      <h1 className="text-xl font-semibold">Create your workspace</h1>
      <p className="mt-1 text-sm text-muted">Free to set up. Connect only the channels you need.</p>

      <form onSubmit={onSubmit} className="mt-6 space-y-4" noValidate>
        {error && <Alert tone="error">{error}</Alert>}
        <Input label="Your name" autoComplete="name" value={form.display_name} onChange={set("display_name")} placeholder="Ada Lovelace" />
        <Input
          label="Workspace name"
          required
          value={form.workspace_name}
          onChange={set("workspace_name")}
          placeholder="Acme Support"
          hint="Usually your company or brand."
        />
        <Input label="Work email" type="email" autoComplete="email" required value={form.email} onChange={set("email")} />
        <Input
          label="Password"
          type="password"
          autoComplete="new-password"
          required
          value={form.password}
          onChange={set("password")}
          error={passwordError}
          hint={`At least ${MIN_PASSWORD} characters. A passphrase works well.`}
        />
        <Button type="submit" full loading={busy} disabled={!ready}>
          Create account
        </Button>
      </form>

      <p className="mt-6 text-center text-sm text-muted">
        Already have an account?{" "}
        <Link href="/login" className="font-medium text-brand hover:underline">
          Sign in
        </Link>
      </p>
    </>
  );
}
