"use client";

import type { ReactNode } from "react";
import { AppShell } from "@/components/app-shell";
import { FullPageSpinner } from "@/components/ui";
import { useRequireAuth } from "@/lib/guards";

export default function AppLayout({ children }: { children: ReactNode }) {
  const pending = useRequireAuth();
  if (pending) return <FullPageSpinner label="Checking your session…" />;
  return <AppShell>{children}</AppShell>;
}
