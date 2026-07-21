"use client";

import { postJson } from "@/lib/api";

const TOKEN_KEY = "z_access_token";
const CREDS_KEY = "z_session";

export type ZSession = {
  access_token: string;
  refresh_token?: string | null;
  expires_at?: number | null;
  user?: {
    id?: string;
    email?: string | null;
    name?: string | null;
    phone?: string | null;
    provider?: string | null;
  };
  workspace?: {
    id?: string | null;
    name?: string | null;
    role?: string | null;
    organization?: string | null;
  };
};

export function loadSession(): ZSession | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(CREDS_KEY);
    if (!raw) return null;
    return JSON.parse(raw) as ZSession;
  } catch {
    return null;
  }
}

export function saveSession(session: ZSession): void {
  localStorage.setItem(CREDS_KEY, JSON.stringify(session));
  if (session.access_token) {
    localStorage.setItem(TOKEN_KEY, session.access_token);
  }
}

export function clearSession(): void {
  localStorage.removeItem(CREDS_KEY);
  localStorage.removeItem(TOKEN_KEY);
}

function isExpired(session: ZSession, skewSeconds = 60): boolean {
  if (!session.expires_at) return false;
  return Date.now() / 1000 >= session.expires_at - skewSeconds;
}

/** Load session, refreshing the access token when expired. */
export async function ensureSession(): Promise<ZSession | null> {
  const session = loadSession();
  if (!session?.access_token) return null;
  if (!isExpired(session)) return session;
  if (!session.refresh_token) {
    clearSession();
    return null;
  }
  const result = await postJson<ZSession & { detail?: string }>(
    "/v1/auth/refresh",
    { refresh_token: session.refresh_token }
  );
  if (!result.ok || !result.data?.access_token) {
    clearSession();
    return null;
  }
  saveSession(result.data);
  return result.data;
}

export function isSignedIn(): boolean {
  const session = loadSession();
  if (!session?.access_token) return false;
  if (isExpired(session) && !session.refresh_token) return false;
  return true;
}
