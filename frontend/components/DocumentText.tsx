"use client";

import { useEffect, useMemo, useRef } from "react";

import type { Entity } from "@/lib/types";

interface Span {
  start: number;
  end: number;
}

/** Renders clinical text with entity highlights (negated = struck through, uncertain = dashed,
 * family = purple) and the currently selected evidence span outlined. */
export function DocumentText({
  text,
  entities,
  evidence,
  sections,
}: {
  text: string;
  entities: Entity[];
  evidence: Span[];
  sections: { name: string; start: number; end: number }[];
}) {
  const ref = useRef<HTMLDivElement>(null);

  const segments = useMemo(() => {
    const cuts = new Set<number>([0, text.length]);
    for (const e of entities) { cuts.add(e.start); cuts.add(e.end); }
    for (const e of evidence) { cuts.add(e.start); cuts.add(e.end); }
    for (const s of sections) cuts.add(s.start);
    const points = [...cuts].filter((c) => c >= 0 && c <= text.length).sort((a, b) => a - b);
    const out: { start: number; end: number; ent?: Entity; ev: boolean; sectionStart?: string }[] = [];
    for (let i = 0; i < points.length - 1; i++) {
      const [s, e] = [points[i], points[i + 1]];
      if (s === e) continue;
      const ent = entities.find((x) => x.start <= s && e <= x.end);
      const ev = evidence.some((x) => x.start <= s && e <= x.end);
      const sec = sections.find((x) => x.start === s);
      out.push({ start: s, end: e, ent, ev, sectionStart: sec?.name });
    }
    return out;
  }, [text, entities, evidence, sections]);

  useEffect(() => {
    ref.current?.querySelector(".evidence-hl")?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [evidence]);

  return (
    <div ref={ref} className="whitespace-pre-wrap break-words font-mono text-[13px] leading-6">
      {segments.map((seg) => {
        const chunk = text.slice(seg.start, seg.end);
        const cls: string[] = [];
        let title: string | undefined;
        if (seg.ent) {
          const e = seg.ent;
          cls.push("ent", `ent-${e.category}`);
          if (e.negated) cls.push("ent-negated");
          if (e.uncertain) cls.push("ent-uncertain");
          if (e.family) cls.push("ent-family");
          const flags = [e.negated && "negated", e.uncertain && "uncertain", e.family && "family",
            e.historical && "historical", e.assertion_conflict && "NLP/LLM conflict"].filter(Boolean);
          title = `${e.category}${e.normalized ? ` → ${e.normalized}` : ""}${flags.length ? ` (${flags.join(", ")})` : ""}`;
        }
        if (seg.ev) cls.push("evidence-hl");
        return cls.length ? (
          <mark key={seg.start} className={cls.join(" ")} title={title} style={{ color: "inherit" }}>{chunk}</mark>
        ) : (
          <span key={seg.start}>{chunk}</span>
        );
      })}
    </div>
  );
}
