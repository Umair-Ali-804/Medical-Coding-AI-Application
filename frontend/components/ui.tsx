import type { ReactNode } from "react";

const TONES: Record<string, string> = {
  green: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  amber: "bg-amber-50 text-amber-800 ring-amber-200",
  red: "bg-red-50 text-red-700 ring-red-200",
  blue: "bg-sky-50 text-sky-700 ring-sky-200",
  slate: "bg-slate-100 text-slate-600 ring-slate-200",
  teal: "bg-teal-50 text-teal-700 ring-teal-200",
  purple: "bg-purple-50 text-purple-700 ring-purple-200",
};

export function Badge({ tone = "slate", children }: { tone?: keyof typeof TONES | string; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${TONES[tone] ?? TONES.slate}`}>
      {children}
    </span>
  );
}

const STATUS_TONE: Record<string, string> = {
  uploaded: "slate",
  processing: "blue",
  text_extracted: "slate",
  entities_extracted: "blue",
  coded: "amber",
  in_review: "amber",
  completed: "green",
  failed: "red",
  pending_review: "amber",
  approved: "green",
  edited: "teal",
  rejected: "red",
  superseded: "slate",
  passed: "green",
  flagged: "amber",
  standard: "green",
  mandatory: "amber",
  system_rejected: "red",
};

export function StatusBadge({ value }: { value: string | null | undefined }) {
  if (!value) return null;
  return <Badge tone={STATUS_TONE[value] ?? "slate"}>{value.replaceAll("_", " ")}</Badge>;
}

export function ConfidenceBar({ value, threshold }: { value: number; threshold?: number }) {
  const pct = Math.round(value * 100);
  const color = value >= (threshold ?? 0.8) ? "bg-emerald-500" : value >= 0.5 ? "bg-amber-500" : "bg-red-500";
  return (
    <div className="flex items-center gap-2" title={`Platform confidence ${pct}%`}>
      <div className="relative h-1.5 w-24 overflow-hidden rounded-full bg-slate-200">
        <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
        {threshold !== undefined && (
          <div className="absolute top-0 h-full w-px bg-slate-700" style={{ left: `${Math.round(threshold * 100)}%` }} />
        )}
      </div>
      <span className="font-mono text-xs tabular-nums text-muted">{pct}%</span>
    </div>
  );
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="card p-4">
      <div className="text-xs font-medium uppercase tracking-wide text-muted">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
      {hint && <div className="mt-0.5 text-xs text-muted">{hint}</div>}
    </div>
  );
}

export function pct(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${Math.round(v * 100)}%`;
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
