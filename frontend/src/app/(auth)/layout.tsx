import type { ReactNode } from "react";
import { Card, Logo } from "@/components/ui";

export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center px-4 py-10">
      <Logo className="mb-6 text-xl" />
      <Card className="w-full max-w-md p-6 sm:p-8">{children}</Card>
      <p className="mt-6 text-xs text-muted">Secure sign-in · Your data stays in your own database</p>
    </main>
  );
}
