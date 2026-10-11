"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { authApi, onSessionChange, refreshSession, type Me } from "@/lib/api";

type Status = "loading" | "authenticated" | "anonymous";

interface AuthState {
  status: Status;
  me: Me | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  reload: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>("loading");
  const [me, setMe] = useState<Me | null>(null);

  const loadMe = useCallback(async () => {
    try {
      setMe(await authApi.me());
      setStatus("authenticated");
    } catch {
      setMe(null);
      setStatus("anonymous");
    }
  }, []);

  // On first load, trade the refresh cookie for an access token (single-flight, so StrictMode is safe).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const session = await refreshSession();
      if (cancelled) return;
      if (session) await loadMe();
      else setStatus("anonymous");
    })();
    return () => {
      cancelled = true;
    };
  }, [loadMe]);

  // If a later refresh fails (revoked, expired), drop to signed-out everywhere.
  useEffect(
    () =>
      onSessionChange((session) => {
        if (!session) {
          setMe(null);
          setStatus("anonymous");
        }
      }),
    [],
  );

  const login = useCallback(
    async (email: string, password: string) => {
      await authApi.login(email, password);
      await loadMe();
    },
    [loadMe],
  );

  const logout = useCallback(async () => {
    await authApi.logout();
    setMe(null);
    setStatus("anonymous");
  }, []);

  const value = useMemo(() => ({ status, me, login, logout, reload: loadMe }), [status, me, login, logout, loadMe]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
