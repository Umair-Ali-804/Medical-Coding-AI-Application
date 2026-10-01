import "server-only";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import type { User } from "./types";

export const SESSION_COOKIE = "mcai_session";
export const USER_COOKIE = "mcai_user";

export function backendUrl(): string {
  return (process.env.BACKEND_URL ?? "http://localhost:8000").replace(/\/$/, "");
}

export async function getToken(): Promise<string | null> {
  return (await cookies()).get(SESSION_COOKIE)?.value ?? null;
}

export async function getUser(): Promise<User | null> {
  const raw = (await cookies()).get(USER_COOKIE)?.value;
  if (!raw) return null;
  try {
    return JSON.parse(Buffer.from(raw, "base64url").toString("utf8")) as User;
  } catch {
    return null;
  }
}

export async function requireUser(): Promise<{ user: User; token: string }> {
  const [user, token] = [await getUser(), await getToken()];
  if (!user || !token) redirect("/login");
  return { user, token };
}

/** Server-side call to the FastAPI backend with the session token. */
export async function backendFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = await getToken();
  if (!token) redirect("/login");
  const res = await fetch(`${backendUrl()}/api/v1${path}`, {
    ...init,
    headers: { ...(init.headers ?? {}), Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  if (res.status === 401) redirect("/login?expired=1");
  if (!res.ok) throw new Error(`Backend ${res.status}: ${await res.text()}`);
  return (await res.json()) as T;
}
