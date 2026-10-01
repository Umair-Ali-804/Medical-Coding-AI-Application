import { NextResponse } from "next/server";

import { SESSION_COOKIE, USER_COOKIE } from "@/lib/session";

export async function POST(req: Request) {
  const out = NextResponse.redirect(new URL("/login", req.url), { status: 303 });
  out.cookies.delete(SESSION_COOKIE);
  out.cookies.delete(USER_COOKIE);
  return out;
}
