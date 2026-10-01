import { NextResponse } from "next/server";

import { backendUrl, SESSION_COOKIE, USER_COOKIE } from "@/lib/session";

export async function POST(req: Request) {
  const { email, password } = (await req.json()) as { email?: string; password?: string };
  if (!email || !password) {
    return NextResponse.json({ error: { message: "Email and password are required" } }, { status: 400 });
  }
  const form = new URLSearchParams({ username: email, password });
  const res = await fetch(`${backendUrl()}/api/v1/auth/login`, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      "X-Forwarded-For": req.headers.get("x-forwarded-for") ?? "",
    },
    body: form,
    cache: "no-store",
  });
  const body = await res.json();
  if (!res.ok) return NextResponse.json(body, { status: res.status });

  const { access_token, expires_in, user } = body as {
    access_token: string;
    expires_in: number;
    user: { id: string; email: string; full_name: string; role: string };
  };
  if (user.role === "service") {
    return NextResponse.json({ error: { message: "Service accounts cannot sign in" } }, { status: 403 });
  }
  const secure = process.env.NODE_ENV === "production" && process.env.COOKIE_INSECURE !== "1";
  const out = NextResponse.json({ user });
  const opts = { httpOnly: true, secure, sameSite: "strict" as const, path: "/", maxAge: expires_in };
  out.cookies.set(SESSION_COOKIE, access_token, opts);
  out.cookies.set(USER_COOKIE, Buffer.from(JSON.stringify(user)).toString("base64url"), opts);
  return out;
}
