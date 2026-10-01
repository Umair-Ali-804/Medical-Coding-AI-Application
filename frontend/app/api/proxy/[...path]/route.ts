/**
 * Backend-for-frontend proxy: forwards /api/proxy/<path> to FastAPI /api/v1/<path>,
 * attaching the session token from the httpOnly cookie.
 */
import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { backendUrl, SESSION_COOKIE } from "@/lib/session";

const PASS_HEADERS = ["content-type", "content-disposition"];

async function handle(req: Request, ctx: { params: Promise<{ path: string[] }> }) {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!token) return NextResponse.json({ error: { message: "Not authenticated" } }, { status: 401 });
  const { path } = await ctx.params;
  if (path.some((p) => p === ".." || p.includes("/"))) {
    return NextResponse.json({ error: { message: "Invalid path" } }, { status: 400 });
  }
  const url = new URL(req.url);
  const target = `${backendUrl()}/api/v1/${path.map(encodeURIComponent).join("/")}${url.search}`;

  const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
  const ctype = req.headers.get("content-type");
  if (ctype) headers["Content-Type"] = ctype;
  const rid = req.headers.get("x-request-id");
  if (rid) headers["X-Request-ID"] = rid;

  const hasBody = !["GET", "HEAD"].includes(req.method);
  const res = await fetch(target, {
    method: req.method,
    headers,
    body: hasBody ? await req.arrayBuffer() : undefined,
    cache: "no-store",
  });
  const out = new NextResponse(res.body, { status: res.status });
  for (const h of PASS_HEADERS) {
    const v = res.headers.get(h);
    if (v) out.headers.set(h, v);
  }
  out.headers.set("Cache-Control", "no-store");
  return out;
}

export const GET = handle;
export const POST = handle;
export const PATCH = handle;
export const DELETE = handle;
