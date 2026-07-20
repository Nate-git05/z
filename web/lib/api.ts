/** Shared API helpers for the Z Next.js client. */

export function apiBase(): string {
  // Browser: prefer same-origin (Next rewrites /v1 → FastAPI).
  // Server components: hit the API host directly.
  if (typeof window !== "undefined") return "";
  return (process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8080").replace(
    /\/$/,
    ""
  );
}

export async function postJson<T>(
  path: string,
  body: Record<string, unknown>
): Promise<{ ok: boolean; status: number; data: T }> {
  const res = await fetch(`${apiBase()}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
    credentials: "include",
  });
  let data = {} as T;
  try {
    data = (await res.json()) as T;
  } catch {
    /* ignore */
  }
  return { ok: res.ok, status: res.status, data };
}
