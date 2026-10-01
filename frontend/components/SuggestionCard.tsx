"use client";

import { useState } from "react";

import { CodeSearch } from "@/components/CodeSearch";
import { Badge, ConfidenceBar, StatusBadge } from "@/components/ui";
import { post } from "@/lib/api";
import { ERROR_CATEGORIES, type Suggestion } from "@/lib/types";

const SEVERITY_TONE = { reject: "red", flag: "amber", info: "blue" } as const;

export function SuggestionCard({
  s,
  selected,
  canReview,
  locked,
  onSelect,
  onChanged,
  onError,
}: {
  s: Suggestion;
  selected: boolean;
  canReview: boolean;
  locked: boolean;
  onSelect: () => void;
  onChanged: () => void;
  onError: (msg: string) => void;
}) {
  const [mode, setMode] = useState<"none" | "edit" | "reject">("none");
  const [code, setCode] = useState("");
  const [reason, setReason] = useState("");
  const [category, setCategory] = useState("");
  const [busy, setBusy] = useState(false);
  const signals = s.confidence_breakdown?.signals ?? {};
  const decided = s.status !== "pending_review";
  const problems = s.validation_issues.filter((i) => i.severity !== "info");
  const hints = s.validation_issues.filter((i) => i.severity === "info");

  async function act(path: string, body?: unknown) {
    setBusy(true);
    try {
      await post(`/suggestions/${s.id}/${path}`, body);
      setMode("none");
      setReason("");
      onChanged();
    } catch (e) {
      onError(e instanceof Error ? e.message : "Action failed");
    } finally {
      setBusy(false);
    }
  }

  const border = selected ? "border-brand ring-2 ring-brand-soft" : s.review_route === "system_rejected" ? "border-red-200" : "border-line";

  return (
    <article className={`rounded-xl border bg-white p-3 transition ${border} ${s.status === "rejected" ? "opacity-60" : ""}`}
      onClick={onSelect}>
      <div className="flex flex-wrap items-start gap-x-3 gap-y-1">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="font-mono text-base font-semibold">{s.status === "edited" ? <><s className="text-muted">{s.code}</s> → {s.final_code}</> : s.code}</span>
            <span className="text-[11px] text-muted">#{s.sequence} {s.source === "manual" ? "· added by coder" : ""}</span>
          </div>
          <div className="text-sm text-slate-700">{s.status === "edited" ? s.final_description : s.description}</div>
        </div>
        <div className="flex flex-col items-end gap-1">
          <StatusBadge value={s.status} />
          {s.source === "ai" && <StatusBadge value={s.review_route} />}
        </div>
      </div>

      {s.source === "ai" && (
        <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-muted">
          <ConfidenceBar value={s.confidence} threshold={s.confidence_breakdown?.threshold} />
          <span>validation: <StatusBadge value={s.validation_status} /></span>
          {s.retrieval_rank ? <span>RAG rank {s.retrieval_rank}</span> : <span className="text-amber-700">not retrieved</span>}
        </div>
      )}

      {s.evidence.length > 0 && (
        <div className="mt-2 space-y-1">
          {s.evidence.map((ev, i) => (
            <blockquote key={i} className="border-l-2 border-teal-300 bg-teal-50/60 px-2 py-1 text-xs text-slate-700">
              “{ev.quote}”
              {ev.match_score < 0.97 && <span className="ml-1 text-amber-700">({Math.round(ev.match_score * 100)}% match)</span>}
            </blockquote>
          ))}
        </div>
      )}

      {problems.length > 0 && (
        <ul className="mt-2 space-y-1">
          {problems.map((iss, i) => (
            <li key={i} className="flex gap-2 text-xs">
              <Badge tone={SEVERITY_TONE[iss.severity]}>{iss.severity}</Badge>
              <span className="text-slate-700">{iss.message}</span>
            </li>
          ))}
        </ul>
      )}
      {hints.length > 0 && (
        <details className="mt-1 text-xs text-muted" onClick={(e) => e.stopPropagation()}>
          <summary className="cursor-pointer select-none">{hints.length} coding hint{hints.length > 1 ? "s" : ""}</summary>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {hints.map((iss, i) => <li key={i}>{iss.message}</li>)}
          </ul>
        </details>
      )}

      {selected && (
        <details className="mt-2 text-xs text-muted" onClick={(e) => e.stopPropagation()}>
          <summary className="cursor-pointer select-none">Why this code?</summary>
          {s.rationale && <p className="mt-1 text-slate-700">{s.rationale}</p>}
          <div className="mt-1 grid grid-cols-3 gap-1 font-mono">
            {Object.entries(signals).map(([k, v]) => (
              <span key={k}>{k}: {v === null || v === undefined ? "n/a" : Number(v).toFixed(2)}</span>
            ))}
          </div>
          {s.llm_confidence !== null && <div className="mt-1">model self-reported: {Math.round((s.llm_confidence ?? 0) * 100)}%</div>}
          <div>calibration: {s.confidence_breakdown?.calibration ?? "—"} · KB {s.kb_version ?? "—"}</div>
        </details>
      )}

      {canReview && !locked && (
        <div className="mt-3 flex flex-wrap gap-2" onClick={(e) => e.stopPropagation()}>
          <button className="btn-primary" disabled={busy || s.validation_status === "rejected" || s.status === "approved"}
            title={s.validation_status === "rejected" ? "Failed hard validation — edit to a valid code or reject" : undefined}
            onClick={() => act("approve")}>Approve</button>
          <button className="btn-ghost" disabled={busy} onClick={() => setMode(mode === "edit" ? "none" : "edit")}>Edit</button>
          <button className="btn-danger" disabled={busy || s.status === "rejected"} onClick={() => setMode(mode === "reject" ? "none" : "reject")}>Reject</button>
          {decided && <span className="self-center text-xs text-muted">Reviewed — you can still change the decision until finalized.</span>}
        </div>
      )}

      {mode !== "none" && (
        <div className="mt-3 space-y-2 rounded-lg bg-slate-50 p-3" onClick={(e) => e.stopPropagation()}>
          {mode === "edit" && <CodeSearch onPick={(c) => setCode(c)} />}
          <input className="input" placeholder={mode === "edit" ? "Reason for change (required)" : "Reason for rejection (required)"}
            value={reason} onChange={(e) => setReason(e.target.value)} />
          <select className="input" value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">Error category (for analysis)</option>
            {ERROR_CATEGORIES.map((c) => <option key={c} value={c}>{c.replaceAll("_", " ")}</option>)}
          </select>
          <div className="flex gap-2">
            {mode === "edit" ? (
              <button className="btn-primary" disabled={busy || !code || reason.length < 3}
                onClick={() => act("edit", { code, reason, error_category: category || null })}>Save {code || "code"}</button>
            ) : (
              <button className="btn-danger" disabled={busy || reason.length < 3}
                onClick={() => act("reject", { reason, error_category: category || null })}>Confirm reject</button>
            )}
            <button className="btn-ghost" onClick={() => setMode("none")}>Cancel</button>
          </div>
        </div>
      )}
    </article>
  );
}
