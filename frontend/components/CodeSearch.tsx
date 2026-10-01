"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";

interface Hit {
  code: string;
  description: string;
  billable: boolean;
}

export function CodeSearch({ onPick, initial = "" }: { onPick: (code: string, description: string) => void; initial?: string }) {
  const [q, setQ] = useState(initial);
  const [hits, setHits] = useState<Hit[]>([]);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (q.trim().length < 2) {
      setHits([]);
      return;
    }
    const t = setTimeout(async () => {
      try {
        setHits(await api<Hit[]>(`/knowledge/search?q=${encodeURIComponent(q)}&limit=10`));
        setOpen(true);
      } catch {
        setHits([]);
      }
    }, 250);
    return () => clearTimeout(t);
  }, [q]);

  return (
    <div className="relative">
      <input className="input font-mono" placeholder="Search ICD-10-CM code or term" value={q}
        onChange={(e) => setQ(e.target.value)} onFocus={() => setOpen(true)} />
      {open && hits.length > 0 && (
        <ul className="absolute z-30 mt-1 max-h-64 w-full overflow-auto rounded-lg border border-line bg-white shadow-lg">
          {hits.map((h) => (
            <li key={h.code}>
              <button type="button" disabled={!h.billable}
                className="flex w-full gap-2 px-3 py-2 text-left text-sm hover:bg-slate-50 disabled:opacity-40"
                onClick={() => { onPick(h.code, h.description); setQ(h.code); setOpen(false); }}>
                <span className="w-20 shrink-0 font-mono font-medium">{h.code}</span>
                <span className="text-slate-600">{h.description}{!h.billable && " (header — not billable)"}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
