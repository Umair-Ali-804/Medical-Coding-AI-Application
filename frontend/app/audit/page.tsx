import Link from "next/link";

import { Shell } from "@/components/Shell";
import { fmtDate } from "@/components/ui";
import { backendFetch, requireUser } from "@/lib/session";
import type { AuditEntry } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function AuditPage({ searchParams }: { searchParams: Promise<{ action?: string; before?: string }> }) {
  const { user } = await requireUser();
  const sp = await searchParams;
  const qs = new URLSearchParams({ limit: "100" });
  if (sp.action) qs.set("action", sp.action);
  if (sp.before) qs.set("before_id", sp.before);
  const rows = await backendFetch<AuditEntry[]>(`/audit?${qs}`);
  const verify = user.role === "admin" ? await backendFetch<{ valid: boolean; checked: number; broken_at_id: number | null }>("/audit/verify") : null;

  return (
    <Shell user={user}>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Audit trail</h1>
          <p className="text-sm text-muted">Append-only, hash-chained record of every access and decision.</p>
        </div>
        {verify && (
          <div className={`rounded-lg px-3 py-2 text-sm ${verify.valid ? "bg-emerald-50 text-emerald-800" : "bg-red-50 text-red-800"}`}>
            {verify.valid ? `Chain verified · ${verify.checked} entries intact` : `Chain BROKEN at entry ${verify.broken_at_id}`}
          </div>
        )}
      </div>
      <form className="mb-3 flex gap-2" action="/audit">
        <input name="action" defaultValue={sp.action} placeholder="Filter by action, e.g. suggestion.edited" className="input w-80" />
        <button className="btn-ghost">Filter</button>
      </form>
      <div className="card overflow-x-auto">
        <table className="w-full min-w-[800px] text-sm">
          <thead className="border-b border-line text-left text-xs uppercase tracking-wide text-muted">
            <tr><th className="px-4 py-2">Time</th><th className="px-4 py-2">Actor</th><th className="px-4 py-2">Action</th><th className="px-4 py-2">Document</th><th className="px-4 py-2">Details</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-b border-line align-top last:border-0">
                <td className="whitespace-nowrap px-4 py-2 text-xs text-muted">{fmtDate(r.ts)}</td>
                <td className="px-4 py-2 text-xs">{r.actor_label ?? r.actor_type}</td>
                <td className="px-4 py-2 font-mono text-xs">{r.action}</td>
                <td className="px-4 py-2 text-xs">{r.document_id ? <Link className="text-brand hover:underline" href={`/documents/${r.document_id}`}>{r.document_id.slice(0, 8)}</Link> : "—"}</td>
                <td className="max-w-[480px] px-4 py-2 font-mono text-[11px] text-slate-600">{JSON.stringify(r.details)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length === 100 && (
        <div className="mt-3 text-right text-sm">
          <Link className="text-brand" href={`/audit?${new URLSearchParams({ ...(sp.action ? { action: sp.action } : {}), before: String(rows[rows.length - 1].id) })}`}>Older →</Link>
        </div>
      )}
    </Shell>
  );
}
