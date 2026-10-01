import Link from "next/link";

import { Shell } from "@/components/Shell";
import { fmtDate, pct, Stat, StatusBadge } from "@/components/ui";
import { backendFetch, requireUser } from "@/lib/session";
import type { DocumentSummary, Stats } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function Dashboard() {
  const { user } = await requireUser();
  const [stats, docs] = await Promise.all([
    backendFetch<Stats>("/stats"),
    backendFetch<{ items: DocumentSummary[]; total: number }>("/documents?limit=8"),
  ]);
  const byStatus = stats.documents_by_status;
  const total = Object.values(byStatus).reduce((a, b) => a + b, 0);
  const errors = Object.entries(stats.error_categories).sort((a, b) => b[1] - a[1]);
  const maxErr = Math.max(1, ...errors.map(([, n]) => n));

  return (
    <Shell user={user}>
      <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Dashboard</h1>
          <p className="text-sm text-muted">Coding throughput, review queue and AI quality from coder feedback.</p>
        </div>
        <Link href="/documents" className="btn-primary">Upload document</Link>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">
        <Stat label="Documents" value={total} />
        <Stat label="Pending review" value={stats.pending_review} hint={`${stats.mandatory_review} mandatory`} />
        <Stat label="Completed" value={byStatus.completed ?? 0} />
        <Stat label="AI acceptance" value={pct(stats.acceptance_rate)} hint="approved / reviewed" />
        <Stat label="Correction rate" value={pct(stats.correction_rate)} hint="edited + rejected + added" />
        <Stat label="LLM spend" value={`$${stats.llm_cost_usd_total.toFixed(2)}`} hint={stats.avg_coding_latency_ms ? `${Math.round(stats.avg_coding_latency_ms)} ms avg coding` : undefined} />
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        <section className="card lg:col-span-2">
          <div className="flex items-center justify-between border-b border-line px-4 py-3">
            <h2 className="font-medium">Recent documents</h2>
            <Link href="/documents" className="text-sm text-brand hover:underline">View all</Link>
          </div>
          <table className="w-full text-sm">
            <tbody>
              {docs.items.length === 0 && (
                <tr><td className="px-4 py-8 text-center text-muted">No documents yet.</td></tr>
              )}
              {docs.items.map((d) => (
                <tr key={d.id} className="border-b border-line last:border-0 hover:bg-slate-50">
                  <td className="px-4 py-2.5">
                    <Link href={`/documents/${d.id}`} className="font-medium hover:text-brand">{d.filename}</Link>
                    <div className="text-xs text-muted">{d.external_id ?? d.id.slice(0, 8)} · {d.encounter_type}</div>
                  </td>
                  <td className="px-4 py-2.5"><StatusBadge value={d.status} /></td>
                  <td className="px-4 py-2.5"><StatusBadge value={d.review_route} /></td>
                  <td className="whitespace-nowrap px-4 py-2.5 text-right text-xs text-muted">{fmtDate(d.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section className="card p-4">
          <h2 className="font-medium">Coder corrections by error type</h2>
          <p className="mb-3 text-xs text-muted">Drives error analysis: fix the component, not the model.</p>
          {errors.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted">No corrections recorded yet.</p>
          ) : (
            <ul className="space-y-2">
              {errors.map(([cat, n]) => (
                <li key={cat} className="text-sm">
                  <div className="flex justify-between"><span>{cat.replaceAll("_", " ")}</span><span className="tabular-nums text-muted">{n}</span></div>
                  <div className="mt-1 h-1.5 rounded-full bg-slate-100"><div className="h-full rounded-full bg-brand" style={{ width: `${(n / maxErr) * 100}%` }} /></div>
                </li>
              ))}
            </ul>
          )}
          <div className="mt-4 grid grid-cols-2 gap-2 border-t border-line pt-3 text-xs text-muted">
            <div>Avg confidence (approved): <b className="text-ink">{pct(stats.avg_confidence_approved)}</b></div>
            <div>Avg confidence (rejected): <b className="text-ink">{pct(stats.avg_confidence_rejected)}</b></div>
          </div>
        </section>
      </div>
    </Shell>
  );
}
