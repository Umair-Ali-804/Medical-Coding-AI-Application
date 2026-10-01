"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { api, ApiError } from "@/lib/api";

type Mode = "file" | "text";

export function UploadPanel() {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>("file");
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const [encounter, setEncounter] = useState("outpatient");
  const [sex, setSex] = useState("");
  const [age, setAge] = useState("");
  const [externalId, setExternalId] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      setBusy("Uploading and extracting text…");
      let docId: string;
      if (mode === "file") {
        if (!file) throw new Error("Choose a PDF, DOCX or TXT file");
        const fd = new FormData();
        fd.append("file", file);
        fd.append("encounter_type", encounter);
        if (sex) fd.append("patient_sex", sex);
        if (age) fd.append("patient_age", age);
        if (externalId) fd.append("external_id", externalId);
        docId = (await api<{ document_id: string }>("/documents", { method: "POST", body: fd })).document_id;
      } else {
        docId = (
          await api<{ document_id: string }>("/documents/text", {
            method: "POST",
            body: JSON.stringify({
              text,
              filename: "pasted-note.txt",
              encounter_type: encounter,
              patient_sex: sex || null,
              patient_age: age ? Number(age) : null,
              external_id: externalId || null,
            }),
          })
        ).document_id;
      }
      setBusy("Extracting entities, retrieving references, coding and validating…");
      await api(`/documents/${docId}/process`, { method: "POST" });
      router.push(`/documents/${docId}`);
    } catch (err) {
      setError(err instanceof ApiError || err instanceof Error ? err.message : "Upload failed");
    } finally {
      setBusy(null);
    }
  }

  return (
    <form onSubmit={submit} className="card h-fit space-y-4 p-4">
      <div className="flex items-center justify-between">
        <h2 className="font-medium">New document</h2>
        <div className="flex rounded-lg border border-line p-0.5 text-xs">
          {(["file", "text"] as Mode[]).map((m) => (
            <button key={m} type="button" onClick={() => setMode(m)}
              className={`rounded-md px-2.5 py-1 ${mode === m ? "bg-brand text-white" : "text-muted"}`}>
              {m === "file" ? "Upload file" : "Paste text"}
            </button>
          ))}
        </div>
      </div>
      {mode === "file" ? (
        <label className="block cursor-pointer rounded-lg border-2 border-dashed border-line p-6 text-center text-sm text-muted hover:border-brand">
          <input type="file" accept=".pdf,.docx,.txt,application/pdf,text/plain" className="hidden"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          {file ? <span className="font-medium text-ink">{file.name}</span> : "Click to choose a PDF, DOCX or TXT (max 20 MB)"}
        </label>
      ) : (
        <textarea className="input h-48 font-mono text-xs" placeholder="Paste a de-identified clinical note…"
          value={text} onChange={(e) => setText(e.target.value)} />
      )}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="label">Encounter</label>
          <select className="input" value={encounter} onChange={(e) => setEncounter(e.target.value)}>
            <option value="outpatient">Outpatient</option>
            <option value="inpatient">Inpatient</option>
          </select>
        </div>
        <div>
          <label className="label">External ID</label>
          <input className="input" value={externalId} onChange={(e) => setExternalId(e.target.value)} placeholder="optional" />
        </div>
        <div>
          <label className="label">Patient sex</label>
          <select className="input" value={sex} onChange={(e) => setSex(e.target.value)}>
            <option value="">Unknown</option>
            <option value="F">Female</option>
            <option value="M">Male</option>
          </select>
        </div>
        <div>
          <label className="label">Age</label>
          <input className="input" type="number" min={0} max={125} value={age} onChange={(e) => setAge(e.target.value)} />
        </div>
      </div>
      {error && <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
      <button className="btn-primary w-full py-2" disabled={!!busy}>{busy ?? "Upload & code"}</button>
      <p className="text-xs text-muted">Only upload data you are authorized to process. Text is encrypted at rest.</p>
    </form>
  );
}
