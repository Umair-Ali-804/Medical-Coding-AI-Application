import Link from "next/link";

import { Shell } from "@/components/Shell";
import { UploadPanel } from "@/components/UploadPanel";
import { fmtDate, StatusBadge } from "@/components/ui";
import { backendFetch, requireUser } from "@/lib/session";
import type { DocumentSummary } from "@/lib/types";

export const dynamic = "force-dynamic";

const STATUSES = ["", "processing", "coded", "in_review", "completed", "failed"];

export default async function DocumentsPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string; route?: string; q?: string; offset?: string }>;
}) {
  const { user } = await requireUser();
  const sp = await searchParams;
  const qs = new URLSearchParams({ limit: "50", offset: sp.offset ?? "0" });
  if (sp.status) qs.set("status", sp.status);
  if (sp.route) qs.set("review_route", sp.route);
  if (sp.q) qs.set("q", sp.q);
  const docs = await backendFetch<{ items: DocumentSummary[]; total: number; offset: number; limit: number }>(
    `/documents?${qs}`,
  );
  const canUpload = user.role === "admin" || user.role === "coder";

  return (
    <Shell user={user}>
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Documents</h1>
          <p className="text-sm text-muted">{docs.total} document(s){sp.route ? ` · route: ${sp.route}` : ""}</p>
        </div>
        <form className="flex flex-wrap gap-2" action="/documents">
          <input name="q" defaultValue={sp.q} placeholder="Filename or external ID" className="input w-56" />
          <select name="status" defaultValue={sp.status ?? ""} className="input w-40">
            {STATUSES.map((s) => (
              <option key={s} value={s}>{s ? s.replaceAll("_", " ") : "All statuses"}</option>
            ))}
          </select>
          <select name="route" defaultValue={sp.route ?? ""} className="input w-40">
            <option value="">Any route</option>
            <option value="mandatory">Mandatory review</option>
            <option value="standard">Standard review</option>
          </select>
          <button className="btn-ghost">Filter</button>
        </form>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_380px]">
        <section className="card overflow-x-auto">
          <table className="w-full min-w-[640px] text-sm">
            <thead className="border-b border-line text-left text-xs uppercase tracking-wide text-muted">
              <tr>
                <th className="px-4 py-2.5">Document</th>
                <th className="px-4 py-2.5">Status</th>
                <th className="px-4 py-2.5">Review route</th>
                <th className="px-4 py-2.5">Source</th>
                <th className="px-4 py-2.5 text-right">Uploaded</th>
              </tr>
            </thead>
            <tbody>
              {docs.items.length === 0 && (
                <tr><td colSpan={5} className="px-4 py-10 text-center text-muted">No documents match.</td></tr>
              )}
              {docs.items.map((d) => (
                <tr key={d.id} className="border-b border-line last:border-0 hover:bg-slate-50">
                  <td className="px-4 py-2.5">
                    <Link href={`/documents/${d.id}`} className="font-medium hover:text-brand">{d.filename}</Link>
                    <div className="text-xs text-muted">
                      {d.external_id ?? d.id.slice(0, 8)} · {d.encounter_type}
                      {d.error ? <span className="text-red-600"> · {d.error.slice(0, 60)}</span> : null}
                    </div>
                  </td>
                  <td className="px-4 py-2.5"><StatusBadge value={d.status} /></td>
                  <td className="px-4 py-2.5"><StatusBadge value={d.review_route} /></td>
                  <td className="px-4 py-2.5 text-muted">{d.source}</td>
                  <td className="whitespace-nowrap px-4 py-2.5 text-right text-xs text-muted">{fmtDate(d.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {docs.total > docs.limit && (
            <div className="flex justify-between border-t border-line px-4 py-2 text-sm">
              {docs.offset > 0 ? (
                <Link className="text-brand" href={`/documents?${new URLSearchParams({ ...sp, offset: String(Math.max(0, docs.offset - docs.limit)) })}`}>← Previous</Link>
              ) : <span />}
              {docs.offset + docs.limit < docs.total && (
                <Link className="text-brand" href={`/documents?${new URLSearchParams({ ...sp, offset: String(docs.offset + docs.limit) })}`}>Next →</Link>
              )}
            </div>
          )}
        </section>
        {canUpload && <UploadPanel />}
      </div>
    </Shell>
  );
}
