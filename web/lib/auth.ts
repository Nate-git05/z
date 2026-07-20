"use client";

const TOKEN_KEY = "z_access_token";
const CREDS_KEY = "z_session";

export type ZSession = {
  access_token: string;
  refresh_token?: string | null;
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

export function isSignedIn(): boolean {
  return Boolean(loadSession()?.access_token);
}
