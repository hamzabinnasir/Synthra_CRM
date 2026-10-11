"use client";

import Link from "next/link";
import { useRouter, usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { useAuth } from "@/lib/auth-context";
import { NAV } from "@/lib/nav";
import { Icon } from "./icons";
import { Logo, cx } from "./ui";

function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  return (
    <nav aria-label="Main" className="flex-1 space-y-6 overflow-y-auto px-3 py-4">
      {NAV.map((group, gi) => (
        <div key={group.title ?? gi}>
          {group.title && (
            <p className="mb-1.5 px-3 text-[11px] font-semibold uppercase tracking-wider text-muted">{group.title}</p>
          )}
          <ul className="space-y-0.5">
            {group.items.map((item) => {
              const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
              const base = "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium";
              if (item.soon) {
                return (
                  <li key={item.href}>
                    <span className={cx(base, "cursor-not-allowed text-muted/60")} aria-disabled="true">
                      <Icon name={item.icon} />
                      <span className="flex-1">{item.label}</span>
                      <span className="rounded bg-surface-2 px-1.5 py-0.5 text-[10px] font-semibold uppercase">Soon</span>
                    </span>
                  </li>
                );
              }
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    onClick={onNavigate}
                    aria-current={active ? "page" : undefined}
                    className={cx(base, active ? "bg-brand-soft text-brand" : "text-muted hover:bg-surface-2 hover:text-foreground")}
                  >
                    <Icon name={item.icon} />
                    {item.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const { me, logout } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [drawer, setDrawer] = useState(false);
  const [menu, setMenu] = useState(false);

  // Close overlays on navigation.
  useEffect(() => {
    setDrawer(false);
    setMenu(false);
  }, [pathname]);

  async function signOut() {
    await logout();
    router.replace("/login");
  }

  const initial = (me?.user.display_name || me?.user.email || "?").trim().charAt(0).toUpperCase();

  return (
    <div className="flex min-h-screen">
      {/* Desktop sidebar */}
      <aside className="sticky top-0 hidden h-screen w-64 shrink-0 flex-col border-r border-border bg-surface lg:flex">
        <div className="flex h-16 items-center border-b border-border px-5">
          <Logo />
        </div>
        <Sidebar />
      </aside>

      {/* Mobile drawer */}
      {drawer && (
        <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
          <button className="absolute inset-0 bg-black/40" aria-label="Close menu" onClick={() => setDrawer(false)} />
          <aside className="relative flex h-full w-72 max-w-[85%] flex-col bg-surface shadow-xl">
            <div className="flex h-16 items-center justify-between border-b border-border px-5">
              <Logo />
              <button onClick={() => setDrawer(false)} aria-label="Close menu" className="rounded-lg p-2 text-muted hover:bg-surface-2">
                <Icon name="close" />
              </button>
            </div>
            <Sidebar onNavigate={() => setDrawer(false)} />
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-16 items-center gap-3 border-b border-border bg-surface/90 px-4 backdrop-blur sm:px-6">
          <button onClick={() => setDrawer(true)} aria-label="Open menu" className="rounded-lg p-2 text-muted hover:bg-surface-2 lg:hidden">
            <Icon name="menu" />
          </button>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold">{me?.tenant.name}</p>
            <p className="truncate text-xs text-muted">Workspace</p>
          </div>

          <div className="relative">
            <button
              onClick={() => setMenu((v) => !v)}
              aria-haspopup="menu"
              aria-expanded={menu}
              className="flex items-center gap-2 rounded-lg p-1.5 hover:bg-surface-2"
            >
              <span className="flex h-8 w-8 items-center justify-center rounded-full bg-brand text-sm font-semibold text-white">{initial}</span>
              <span className="hidden max-w-[10rem] truncate text-sm sm:block">{me?.user.display_name || me?.user.email}</span>
            </button>
            {menu && (
              <>
                <button className="fixed inset-0 z-10 cursor-default" aria-label="Close menu" onClick={() => setMenu(false)} />
                <div role="menu" className="absolute right-0 z-20 mt-2 w-60 rounded-xl border border-border bg-surface p-1.5 shadow-lg">
                  <div className="border-b border-border px-3 py-2">
                    <p className="truncate text-sm font-medium">{me?.user.display_name || "Account"}</p>
                    <p className="truncate text-xs text-muted">{me?.user.email}</p>
                  </div>
                  <button
                    role="menuitem"
                    onClick={signOut}
                    className="mt-1 flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm text-foreground hover:bg-surface-2"
                  >
                    <Icon name="logout" />
                    Sign out
                  </button>
                </div>
              </>
            )}
          </div>
        </header>

        <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 sm:px-6 lg:py-8">{children}</main>
      </div>
    </div>
  );
}
