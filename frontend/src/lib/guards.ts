"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { useAuth } from "./auth-context";

/** For login/register: signed-in visitors go straight to the app. Returns true while that decision is pending. */
export function useRedirectIfAuthenticated(to = "/dashboard"): boolean {
  const { status } = useAuth();
  const router = useRouter();
  useEffect(() => {
    if (status === "authenticated") router.replace(to);
  }, [status, router, to]);
  return status === "loading" || status === "authenticated";
}

/** For app pages: signed-out visitors go to /login. Returns true while the session is still being resolved. */
export function useRequireAuth(): boolean {
  const { status } = useAuth();
  const router = useRouter();
  useEffect(() => {
    if (status === "anonymous") router.replace("/login");
  }, [status, router]);
  return status !== "authenticated";
}
