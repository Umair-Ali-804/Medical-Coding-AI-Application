"use client";

/** Browser-side API client. All calls go through the Next.js proxy, which attaches the
 * httpOnly session token server-side (the token is never exposed to JavaScript). */

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public details?: unknown,
  ) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const isForm = typeof FormData !== "undefined" && init.body instanceof FormData;
  const res = await fetch(`/api/proxy${path}`, {
    ...init,
    headers: isForm ? init.headers : { "Content-Type": "application/json", ...(init.headers ?? {}) },
  });
  if (res.status === 401) {
    window.location.href = "/login?expired=1";
    throw new ApiError(401, "Session expired");
  }
  const ctype = res.headers.get("content-type") ?? "";
  const body = ctype.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) {
    const err = (body as { error?: { message?: string; details?: unknown } })?.error;
    throw new ApiError(res.status, err?.message ?? `Request failed (${res.status})`, err?.details);
  }
  return body as T;
}

export const post = <T>(path: string, data?: unknown) =>
  api<T>(path, { method: "POST", body: data === undefined ? undefined : JSON.stringify(data) });
