"""Procedure concepts -> CPT/HCPCS (professional/outpatient) or ICD-10-PCS (inpatient facility) candidates.

Only procedures documented as PERFORMED in this encounter are coded: planned ("Plan: colonoscopy next
month"), historical ("s/p cholecystectomy"), negated and family-history mentions are dropped.
Clinical wording is expanded into each code set's vocabulary before retrieval (e.g. "laparoscopic
cholecystectomy" -> PCS "Resection of Gallbladder, Percutaneous Endoscopic Approach").
"""

from __future__ import annotations

import re

from app.coding.models import ConceptGroup
from app.extraction.context import sentence_spans
from app.extraction.types import Entity
from app.ingestion.sections import Section, section_at
from app.rag.retriever import HybridRetriever, RetrievedCode

NOT_PERFORMED_SECTIONS = {
    "plan",
    "past_surgical_history",
    "past_medical_history",
    "family_history",
    "allergies",
}
PROCEDURE_SECTIONS = {"procedures"}

# clinical term -> extra words in CPT / PCS vocabulary
_EXPANSIONS: list[tuple[re.Pattern, str, str]] = [
    # (pattern, CPT/HCPCS words, PCS words)
    (re.compile(r"laparoscop", re.I), "laparoscopy laparoscopic", "percutaneous endoscopic approach"),
    (re.compile(r"arthroscop", re.I), "arthroscopy", "percutaneous endoscopic approach"),
    (re.compile(r"appendectomy", re.I), "appendectomy", "resection appendix"),
    (re.compile(r"cholecystectomy", re.I), "cholecystectomy", "resection gallbladder"),
    (re.compile(r"colectomy", re.I), "colectomy", "resection colon"),
    (re.compile(r"hysterectomy", re.I), "hysterectomy", "resection uterus"),
    (re.compile(r"mastectomy", re.I), "mastectomy", "resection breast"),
    (re.compile(r"tonsillectomy", re.I), "tonsillectomy", "resection tonsils"),
    (re.compile(r"meniscectomy", re.I), "meniscectomy", "excision meniscus"),
    (
        re.compile(r"colonoscopy", re.I),
        "colonoscopy flexible",
        "inspection lower intestinal tract via natural or artificial opening endoscopic",
    ),
    (
        re.compile(r"\begd\b|esophagogastroduodenoscopy|upper endoscopy", re.I),
        "esophagogastroduodenoscopy",
        "inspection upper intestinal tract via natural or artificial opening endoscopic",
    ),
    (
        re.compile(r"knee (replacement|arthroplasty)|total knee", re.I),
        "arthroplasty knee",
        "replacement knee joint synthetic substitute",
    ),
    (
        re.compile(r"hip (replacement|arthroplasty)|total hip", re.I),
        "arthroplasty hip",
        "replacement hip joint synthetic substitute",
    ),
    (
        re.compile(r"cardiac cath|heart cath|coronary angiogra", re.I),
        "catheter placement coronary angiography",
        "fluoroscopy coronary arteries",
    ),
    (re.compile(r"echocardiogra|\becho\b", re.I), "echocardiography transthoracic", "ultrasonography heart"),
    (
        re.compile(r"\b(ekg|ecg|electrocardiogram)\b", re.I),
        "electrocardiogram routine ecg interpretation report",
        "measurement cardiac electrical activity",
    ),
    (re.compile(r"chest x-?ray|\bcxr\b", re.I), "radiologic examination chest", "plain radiography chest"),
    (re.compile(r"\bct\b|computed tomography", re.I), "computed tomography", "computerized tomography"),
    (
        re.compile(r"\bmri\b|magnetic resonance", re.I),
        "magnetic resonance imaging",
        "magnetic resonance imaging",
    ),
    (re.compile(r"biopsy", re.I), "biopsy", "excision diagnostic"),
    (re.compile(r"incision and drainage|\bi&d\b", re.I), "incision drainage abscess", "drainage"),
    (
        re.compile(r"laceration repair|repair of (a |the )?laceration|sutur", re.I),
        "repair simple wound",
        "repair skin",
    ),
    (
        re.compile(r"(joint|knee|shoulder) injection|arthrocentesis|intra-?articular", re.I),
        "arthrocentesis aspiration injection major joint",
        "introduction joint",
    ),
    (
        re.compile(r"flu (vaccine|shot)|influenza vacc", re.I),
        "influenza virus vaccine administration",
        "introduction serum toxoid vaccine",
    ),
    (re.compile(r"spirometry", re.I), "spirometry", "measurement respiratory"),
    (re.compile(r"intubat", re.I), "intubation endotracheal", "insertion endotracheal airway"),
    (re.compile(r"mechanical ventilation", re.I), "ventilation assist", "assistance respiratory ventilation"),
    (re.compile(r"transfusion", re.I), "transfusion blood", "transfusion"),
    (
        re.compile(r"dexamethasone", re.I),
        "injection dexamethasone sodium phosphate",
        "introduction anti-inflammatory",
    ),
]
_PERFORMED = re.compile(
    r"\b(performed|underwent|was done|were done|completed|administered|given|placed|injected|obtained|"
    r"drained|repaired|removed|excised|inserted|we did|today)\b",
    re.I,
)
_PROC_WORD = re.compile(
    r"(ectomy|otomy|ostomy|oscopy|plasty|pexy|rrhaphy|centesis|graphy|gram)\b|\b(repair|injection|injected|biopsy|"
    r"excision|drainage|insertion|removal|vaccine|vaccination|immunization|administration|administered|x-?ray|"
    r"imaging|ultrasound|catheter\w*|intubation|transfusion|suture\w*|debridement|infusion|reduction|"
    r"arthroplasty|replacement|ekg|ecg|spirometry|mri|ct scan|nebulizer|splint\w*|cast)\b",
    re.I,
)
_BOILERPLATE = re.compile(
    r"^((the )?patient )?tolerated|^(after )?informed consent (was )?obtained\.?$|^no (immediate )?complications|"
    r"^estimated blood loss|^ebl\b|^anesthesia\s*:|^specimens?\s*:|^indications?\s*:|^findings\s*:",
    re.I,
)
_PLANNED = re.compile(
    r"\b(will|plan(ned)?|schedul\w*|consider\w*|recommend\w*|refer\w*|next (week|month|visit)|"
    r"future|if needed|as needed|prn|would|should)\b",
    re.I,
)


def expand_query(text: str, system: str) -> str:
    extra = [
        (cpt if system in ("CPT", "HCPCS") else pcs) for pat, cpt, pcs in _EXPANSIONS if pat.search(text)
    ]
    return f"{text} {' '.join(extra)}".strip()


def _sentence(text: str, pos: int) -> tuple[int, int]:
    for s, e in sentence_spans(text):
        if s <= pos < e:
            return s, e
    return pos, min(len(text), pos + 200)


def build_procedure_groups(text: str, sections: list[Section], entities: list[Entity]) -> list[ConceptGroup]:
    groups: dict[str, ConceptGroup] = {}
    for e in entities:
        if e.category != "procedure" or e.negated or e.family or e.hypothetical:
            continue
        if e.section in NOT_PERFORMED_SECTIONS or e.historical:
            continue
        s, en = _sentence(text, e.start)
        sentence = text[s:en]
        if (
            _PLANNED.search(sentence)
            and not _PERFORMED.search(sentence)
            and e.section not in PROCEDURE_SECTIONS
        ):
            continue
        key = f"proc:{(e.normalized or e.text).lower()}"
        if key in groups:
            groups[key].mentions.append(e)
            continue
        groups[key] = ConceptGroup(
            key=key,
            mentions=[e],
            representative=e,
            query=e.text
            if not e.normalized or e.normalized.lower() in e.text.lower()
            else f"{e.text} {e.normalized}",
            affirmed=True,
            negated=False,
            uncertain=False,
            family=False,
            historical_only=False,
            in_diagnostic_section=False,
            kind="procedure",
        )
    # Sentences of an explicit "Procedure(s) performed" section (and stand-alone "... administered/performed"
    # sentences elsewhere), even when the lexicon has no term for the procedure
    spans: list[tuple[int, int, str | None]] = []
    for sec in sections:
        if sec.name in PROCEDURE_SECTIONS:
            spans.extend(
                (max(a, sec.start), b, sec.name)
                for a, b in sentence_spans(text)
                if a < sec.end and b > sec.start
            )
    for a, b in sentence_spans(text):
        sec_name = section_at(sections, a)
        if sec_name in NOT_PERFORMED_SECTIONS or sec_name in PROCEDURE_SECTIONS:
            continue
        chunk = text[a:b]
        if _PROC_WORD.search(chunk) and _PERFORMED.search(chunk) and not _PLANNED.search(chunk):
            spans.append((a, b, sec_name))
    for a, b, sec_name in spans:
        for m in re.finditer(r"[^\n]+", text[a:b]):
            raw = m.group(0)
            line = raw.strip(" -*\t0123456789.)")
            line = re.sub(r"^(procedures?( performed)?|operations?)\s*:\s*", "", line, flags=re.I).strip()
            if len(line) < 6 or not _PROC_WORD.search(line) or _BOILERPLATE.search(line):
                continue
            start = a + m.start() + max(0, raw.find(line[:12]))
            end = start + len(line)
            if any(g.representative.start < end and start < g.representative.end for g in groups.values()):
                continue  # already covered by a lexicon/LLM procedure mention
            ent = Entity(
                text=line[:200],
                category="procedure",
                start=start,
                end=start + min(len(line), 200),
                section=sec_name,
                source="section",
            )
            key = f"proc:{line.lower()[:60]}"
            groups.setdefault(
                key,
                ConceptGroup(
                    key=key,
                    mentions=[ent],
                    representative=ent,
                    query=line[:200],
                    affirmed=True,
                    negated=False,
                    uncertain=False,
                    family=False,
                    historical_only=False,
                    in_diagnostic_section=False,
                    kind="procedure",
                ),
            )
    out = list(groups.values())
    for g in out:
        g.representative.section = g.representative.section or section_at(sections, g.representative.start)
    return out


def retrieve_procedures(
    groups: list[ConceptGroup], retrievers: dict[str, HybridRetriever], k: int = 8
) -> None:
    """Fill `candidates` of each procedure group from every procedure code set, merged by score."""
    if not groups or not retrievers:
        return
    for system, r in retrievers.items():
        queries = [expand_query(g.query, system) for g in groups]
        for g, cands in zip(groups, r.retrieve_many(queries, k), strict=True):
            g.candidates.extend(cands)
    for g in groups:
        best: dict[str, RetrievedCode] = {}
        for c in g.candidates:
            if c.code not in best or c.score > best[c.code].score:
                best[c.code] = c
        g.candidates = sorted(best.values(), key=lambda c: (-c.score, c.info.system, c.code))[:k]
        for i, c in enumerate(g.candidates, start=1):
            c.rank = i


def procedure_systems_for(encounter_type: str, available: list[str]) -> list[str]:
    wanted = ["ICD-10-PCS"] if encounter_type == "inpatient" else ["CPT", "HCPCS"]
    return [s for s in wanted if s in available]
