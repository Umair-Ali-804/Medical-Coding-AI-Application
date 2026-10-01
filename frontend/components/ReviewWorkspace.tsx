"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { CodeSearch } from "@/components/CodeSearch";
import { DocumentText } from "@/components/DocumentText";
import { SuggestionCard } from "@/components/SuggestionCard";
import { Badge, fmtDate, StatusBadge } from "@/components/ui";
import { api, post } from "@/lib/api";
import type { AuditEntry, DocumentDetail, ModelRun, Role, Suggestion } from "@/lib/types";

type Tab = "codes" | "entities" | "runs" | "audit";

export function ReviewWorkspace({ documentId, role }: { documentId: string; role: Role }) {
  const [doc, setDoc] = useState<DocumentDetail | null>(null);
  const [sugg, setSugg] = useState<Suggestion[]>([]);
  const [runs, setRuns] = useState<ModelRun[]>([]);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("codes");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [addCode, setAddCode] = useState("");
  const [addReason, setAddReason] = useState("");

  const canReview = role === "coder" || role === "admin";
  const locked = doc?.status === "completed";

  const load = useCallback(async () => {
    try {
      const [d, s] = await Promise.all([
        api<DocumentDetail>(`/documents/${documentId}`),
        api<Suggestion[]>(`/documents/${documentId}/suggestions`),
      ]);
      setDoc(d);
      setSugg(s);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
    }
  }, [documentId]);

  useEffect(() => { load(); }, [load]);

  // poll while the background worker is processing
  useEffect(() => {
    if (doc?.status !== "processing") return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [doc?.status, load]);

  useEffect(() => {
    if (tab === "runs") api<ModelRun[]>(`/documents/${documentId}/runs`).then(setRuns).catch(() => {});
    if (tab === "audit") api<AuditEntry[]>(`/documents/${documentId}/audit`).then(setAudit).catch(() => {});
  }, [tab, documentId, sugg]);

  const current = sugg.find((s) => s.id === selected) ?? null;
  const evidenceSpans = useMemo(
    () => (current?.evidence ?? []).filter((e) => e.start !== null && e.end !== null).map((e) => ({ start: e.start!, end: e.end! })),
    [current],
  );
  const counts = useMemo(() => ({
    pending: sugg.filter((s) => s.status === "pending_review").length,
    accepted: sugg.filter((s) => s.status === "approved" || s.status === "edited").length,
  }), [sugg]);

  async function run(label: string, fn: () => Promise<unknown>) {
    setBusy(label);
    setError(null);
    try {
      await fn();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Action failed");
    } finally {
      setBusy(null);
    }
  }

  if (!doc) {
    return <div className="py-20 text-center text-muted">{error ?? "Loading…"}</div>;
  }

  const ordered = [...sugg].sort((a, b) => {
    const rank = { pending_review: 0, approved: 1, edited: 1, rejected: 2, superseded: 3 } as const;
    return rank[a.status] - rank[b.status] || a.sequence - b.sequence;
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">{doc.filename}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted">
            <StatusBadge value={doc.status} />
            <StatusBadge value={doc.review_route} />
            <span>{doc.encounter_type}</span>
            {doc.patient_sex && <span>· sex {doc.patient_sex}</span>}
            {doc.patient_age !== null && <span>· age {doc.patient_age}</span>}
            <span>· {doc.extraction_method}</span>
            <span>· uploaded {fmtDate(doc.created_at)}</span>
          </div>
          {doc.error && <p className="mt-2 text-sm text-red-700">{doc.error}</p>}
        </div>
        <div className="flex flex-wrap gap-2">
          {canReview && !locked && (
            <button className="btn-ghost" disabled={!!busy}
              onClick={() => run("Re-coding…", () => post(`/documents/${documentId}/process`))}>
              {busy === "Re-coding…" ? busy : sugg.length ? "Re-run AI coding" : "Run AI coding"}
            </button>
          )}
          {canReview && !locked && (
            <button className="btn-primary" disabled={!!busy || counts.pending > 0 || counts.accepted === 0}
              title={counts.pending > 0 ? `${counts.pending} suggestion(s) still pending` : undefined}
              onClick={() => run("Finalizing…", () => post(`/documents/${documentId}/finalize`))}>
              Finalize codes
            </button>
          )}
          {locked && (
            <>
              <a className="btn-ghost" href={`/api/proxy/documents/${documentId}/export?format=csv`}>Export CSV</a>
              <a className="btn-ghost" href={`/api/proxy/documents/${documentId}/export?format=json`}>Export JSON</a>
              {role === "admin" && (
                <button className="btn-danger" disabled={!!busy} onClick={() => {
                  const reason = prompt("Reason for reopening this finalized document?");
                  if (reason) run("Reopening…", () => post(`/documents/${documentId}/reopen`, { reason }));
                }}>Reopen</button>
              )}
            </>
          )}
        </div>
      </div>

      {error && (
        <div className="flex items-start justify-between rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          <span>{error}</span>
          <button onClick={() => setError(null)} aria-label="Dismiss">×</button>
        </div>
      )}
      {doc.status === "processing" && (
        <div className="rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-sm text-sky-800">
          Processing in the background — this page refreshes automatically.
        </div>
      )}

      {locked && doc.final_codes.length > 0 && (
        <section className="card p-4">
          <h2 className="mb-2 font-medium">Final codes</h2>
          <div className="flex flex-wrap gap-2">
            {doc.final_codes.map((c) => (
              <div key={c.code} className="rounded-lg border border-line px-3 py-2 text-sm">
                <span className="font-mono font-semibold">{c.sequence}. {c.code}</span>
                <span className="ml-2 text-slate-600">{c.description}</span>
                {!c.ai_suggested && <Badge tone="purple">coder-added</Badge>}
                {c.ai_modified && <Badge tone="teal">edited</Badge>}
              </div>
            ))}
          </div>
        </section>
      )}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_520px]">
        <section className="card flex max-h-[calc(100vh-220px)] min-h-[400px] flex-col">
          <div className="flex flex-wrap items-center gap-3 border-b border-line px-4 py-2 text-xs">
            <span className="font-medium text-ink">Clinical document</span>
            <span className="ent ent-condition">condition</span>
            <span className="ent ent-symptom">symptom</span>
            <span className="ent ent-medication">medication</span>
            <span className="ent ent-negated">negated</span>
            <span className="ent ent-uncertain">uncertain</span>
            <span className="ent ent-family">family</span>
            <span className="evidence-hl px-1">selected evidence</span>
          </div>
          <div className="flex-1 overflow-auto p-4">
            <DocumentText text={doc.text ?? ""} entities={doc.entities} evidence={evidenceSpans} sections={doc.sections} />
          </div>
        </section>

        <section className="flex max-h-[calc(100vh-220px)] min-h-[400px] flex-col">
          <div className="mb-2 flex gap-1 rounded-lg border border-line bg-white p-1 text-sm">
            {([["codes", `Codes (${counts.pending} pending)`], ["entities", `Entities (${doc.entities.length})`], ["runs", "Model runs"], ["audit", "Audit"]] as [Tab, string][]).map(([t, label]) => (
              <button key={t} onClick={() => setTab(t)} className={`flex-1 rounded-md px-2 py-1.5 ${tab === t ? "bg-brand text-white" : "text-muted hover:bg-slate-50"}`}>{label}</button>
            ))}
          </div>
          <div className="flex-1 space-y-2 overflow-auto pr-1">
            {tab === "codes" && (
              <>
                {ordered.length === 0 && <p className="card p-6 text-center text-sm text-muted">No suggestions yet. Run AI coding to generate them.</p>}
                {ordered.map((s) => (
                  <SuggestionCard key={s.id} s={s} selected={s.id === selected} canReview={canReview} locked={!!locked}
                    onSelect={() => setSelected(s.id === selected ? null : s.id)} onChanged={load} onError={setError} />
                ))}
                {canReview && !locked && (
                  <div className="card space-y-2 p-3">
                    <div className="text-sm font-medium">Add a code the AI missed</div>
                    <CodeSearch onPick={(c) => setAddCode(c)} />
                    <input className="input" placeholder="Reason / documentation reference" value={addReason} onChange={(e) => setAddReason(e.target.value)} />
                    <button className="btn-ghost" disabled={!addCode || addReason.length < 3 || !!busy}
                      onClick={() => run("Adding…", async () => {
                        await post(`/documents/${documentId}/suggestions`, { code: addCode, reason: addReason });
                        setAddCode(""); setAddReason("");
                      })}>Add {addCode}</button>
                  </div>
                )}
              </>
            )}
            {tab === "entities" && (
              <div className="card divide-y divide-line">
                {doc.entities.map((e) => (
                  <div key={e.id} className="flex flex-wrap items-center gap-2 px-3 py-2 text-sm">
                    <span className="font-medium">{e.text}</span>
                    <Badge>{e.category}</Badge>
                    {e.negated && <Badge tone="slate">negated</Badge>}
                    {e.uncertain && <Badge tone="amber">uncertain</Badge>}
                    {e.family && <Badge tone="purple">family</Badge>}
                    {e.historical && <Badge tone="blue">historical</Badge>}
                    {e.assertion_conflict && <Badge tone="red">NLP/LLM conflict</Badge>}
                    {e.laterality && <Badge tone="teal">{e.laterality}</Badge>}
                    <span className="ml-auto text-xs text-muted">{e.section ?? "—"} · {e.source}</span>
                  </div>
                ))}
              </div>
            )}
            {tab === "runs" && (
              <div className="space-y-2">
                {runs.map((r) => (
                  <div key={r.id} className="card p-3 text-xs">
                    <div className="flex items-center gap-2 text-sm"><b>{r.run_type}</b><StatusBadge value={r.status === "succeeded" ? "passed" : "rejected"} /><span className="text-muted">{fmtDate(r.created_at)}</span></div>
                    <div className="mt-1 grid grid-cols-2 gap-x-4 gap-y-0.5 font-mono text-muted">
                      <span>model: {r.model}</span><span>provider: {r.provider}</span>
                      <span>prompt: {r.prompt_version ?? "—"}</span><span>KB: {r.kb_version ?? "—"}</span>
                      <span className="col-span-2 truncate">retrieval: {r.retrieval_version ?? "—"}</span>
                      <span>tokens: {r.input_tokens}/{r.output_tokens}</span><span>cost: ${r.cost_usd.toFixed(4)} · {r.latency_ms} ms</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
            {tab === "audit" && (
              <ol className="card divide-y divide-line text-xs">
                {audit.map((a) => (
                  <li key={a.id} className="px-3 py-2">
                    <div className="flex justify-between"><b className="font-mono">{a.action}</b><span className="text-muted">{fmtDate(a.ts)}</span></div>
                    <div className="text-muted">{a.actor_label ?? a.actor_type} · hash {a.hash.slice(0, 12)}…</div>
                    {Object.keys(a.details).length > 0 && <pre className="mt-1 overflow-x-auto text-[11px] text-slate-600">{JSON.stringify(a.details)}</pre>}
                  </li>
                ))}
              </ol>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
