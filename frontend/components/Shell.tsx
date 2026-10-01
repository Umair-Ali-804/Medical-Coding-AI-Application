import Link from "next/link";
import type { ReactNode } from "react";

import type { User } from "@/lib/types";

const NAV = [
  { href: "/", label: "Dashboard" },
  { href: "/documents", label: "Documents" },
  { href: "/documents?route=mandatory", label: "Mandatory review" },
  { href: "/audit", label: "Audit trail", roles: ["admin", "auditor", "coder"] },
];

export function Shell({ user, children }: { user: User; children: ReactNode }) {
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b border-line bg-white/90 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-[1500px] items-center gap-6 px-4 sm:px-6">
          <Link href="/" className="flex items-center gap-2 font-semibold">
            <span className="grid h-7 w-7 place-items-center rounded-md bg-brand text-xs font-bold text-white">Rx</span>
            <span className="hidden sm:inline">Medical Coding AI</span>
          </Link>
          <nav className="flex items-center gap-1 overflow-x-auto text-sm">
            {NAV.filter((n) => !n.roles || n.roles.includes(user.role)).map((n) => (
              <Link key={n.href} href={n.href} className="whitespace-nowrap rounded-md px-2.5 py-1.5 text-slate-600 hover:bg-slate-100 hover:text-ink">
                {n.label}
              </Link>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-sm">
            <span className="hidden text-muted md:inline">
              {user.full_name || user.email} · <span className="font-medium text-ink">{user.role}</span>
            </span>
            <form action="/api/auth/logout" method="post">
              <button className="btn-ghost whitespace-nowrap" type="submit">Sign out</button>
            </form>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-[1500px] px-4 py-6 sm:px-6">{children}</main>
      <footer className="mx-auto max-w-[1500px] px-6 pb-8 text-xs text-muted">
        AI suggestions are decision support only. A certified coder makes every final coding decision.
      </footer>
    </div>
  );
}
