"""Deterministic coder rules: ICD-10-CM Official Guidelines and Alphabetic Index conventions.

Runs after ANY coding model (LLM or heuristic) and before validation. A certified coder applies these
conventions mechanically; a retrieval system or an LLM frequently misses them (e.g. reports I10 next to
heart failure, or an acute MI code for "history of MI"). Every rule:

  * acts only when the documentation clearly supports it (affirmed mentions via ConText; negated,
    family, hypothetical and - where relevant - historical mentions are ignored),
  * adds only codes that exist and are billable in the loaded knowledge base,
  * quotes the exact sentence(s) it relied on as evidence (verbatim, so validation can verify them),
  * records what it changed and why (guideline citation) in the rationale and in `not_coded`,
    so the human coder sees every automatic change.

Rules never remove a code the documentation supports; they replace or remove codes that contradict a
guideline or lack the documentation their definition requires.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.extraction.context import ConTextAnalyzer, sentence_bounds
from app.extraction.types import Entity
from app.ingestion.sections import Section, section_at
from app.knowledge.codes import normalize_code
from app.knowledge.repository import KBSnapshot
from app.llm.schemas import LLMCode, NotCoded

RULES_VERSION = "guideline-rules-1.0"
ICD = "ICD-10-CM"


@dataclass
class RuleChange:
    rule: str
    guideline: str
    removed: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "rule": self.rule,
            "guideline": self.guideline,
            "removed": self.removed,
            "added": self.added,
            "note": self.note,
        }


def _rx(*alts: str) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(alts) + r")", re.IGNORECASE)


# --------------------------------------------------------------------------- documentation helpers
class Doc:
    """The note with assertion-aware phrase search (ConText decides negated/family/uncertain/historical)."""

    def __init__(self, text: str, sections: list[Section], encounter_type: str):
        self.text = text
        self.sections = sections
        self.encounter_type = encounter_type
        self._ctx = ConTextAnalyzer()
        self._cache: dict[str, list[Entity]] = {}

    def mentions(self, pattern: re.Pattern[str]) -> list[Entity]:
        key = pattern.pattern
        if key not in self._cache:
            ents = [
                Entity(
                    text=m.group(0),
                    category="finding",
                    start=m.start(),
                    end=m.end(),
                    section=section_at(self.sections, m.start()),
                    source="guideline-rule",
                )
                for m in pattern.finditer(self.text)
            ]
            self._cache[key] = self._ctx.analyze(self.text, ents) if ents else []
        return self._cache[key]

    def affirmed(self, pattern: re.Pattern[str], *, current: bool = False) -> list[Entity]:
        """Mentions about the patient that are present (not negated/family/hypothetical).

        Uncertain mentions count only for inpatient encounters (Guidelines II.H / III.C); `current=True`
        also drops historical mentions ("history of", "s/p")."""
        out = []
        for e in self.mentions(pattern):
            if e.negated or e.family or e.hypothetical:
                continue
            if e.uncertain and self.encounter_type != "inpatient":
                continue
            if current and e.historical:
                continue
            out.append(e)
        return out

    def has(self, pattern: re.Pattern[str], **kw) -> bool:
        return bool(self.affirmed(pattern, **kw))

    def raw(self, pattern: re.Pattern[str]) -> bool:
        return bool(pattern.search(self.text))

    def sentence(self, pos: int) -> str:
        s, e = sentence_bounds(self.text, pos)
        return self.text[s:e].strip()

    def evidence(self, *patterns: re.Pattern[str]) -> list[str]:
        quotes: list[str] = []
        for p in patterns:
            hits = self.affirmed(p) or self.mentions(p)
            if hits:
                q = self.sentence(hits[0].start)
                q = q if len(q) <= 240 else hits[0].text
                if q and q not in quotes:
                    quotes.append(q)
        return quotes[:3]


# --------------------------------------------------------------------------- vocabulary
HTN = _rx(
    r"hypertension", r"HTN\b", r"high blood pressure", r"hypertensive (?:heart|kidney|chronic|renal|disease)"
)
NOT_ESSENTIAL_HTN = _rx(
    r"pulmonary hypertension",
    r"portal hypertension",
    r"intracranial hypertension",
    r"ocular hypertension",
    r"gestational hypertension",
    r"pre-?eclampsia",
    r"secondary hypertension",
    r"white coat",
)
HTN_UNRELATED = _rx(
    r"(?:unrelated|not related|not due) to (?:the |his |her )?(?:hypertension|HTN|high blood pressure)",
    r"non-?hypertensive",
)
HEART_FAILURE = _rx(
    r"heart failure", r"\bCHF\b", r"HFrEF", r"HFpEF", r"HFmrEF", r"cardiac failure", r"congestive heart"
)
CKD = _rx(
    r"chronic kidney disease",
    r"\bCKD\b",
    r"chronic renal (?:disease|insufficiency|failure)",
    r"end[- ]stage renal",
    r"end[- ]stage kidney",
    r"\bESRD\b",
    r"\bESKD\b",
)
CKD_STAGE = re.compile(
    r"(?:chronic kidney disease|\bCKD\b|chronic renal (?:disease|insufficiency|failure))[^.\n;]{0,30}?"
    r"(?:stage|stg)?\s*\b(V|IV|III\s*[ab]?|II|I|[1-5]\s*[ab]?)\b(?![\d.])",
    re.IGNORECASE,
)
ESRD = _rx(r"end[- ]stage renal", r"end[- ]stage kidney", r"\bESRD\b", r"\bESKD\b")
DIALYSIS = _rx(r"hemodialysis", r"\bdialysis\b", r"peritoneal dialysis")
CKD_OTHER_CAUSE = re.compile(
    r"(?:chronic kidney disease|\bCKD\b)[^.\n]{0,40}\b(?:due to|secondary to|from)\b(?![^.\n]{0,25}diabet)",
    re.IGNORECASE,
)
DM_UNRELATED = _rx(r"(?:unrelated|not related|not due) to (?:the |his |her )?diabet")

INSULIN = _rx(
    r"insulin",
    r"glargine",
    r"lispro",
    r"aspart",
    r"detemir",
    r"degludec",
    r"\bNPH\b",
    r"humalog",
    r"novolog",
    r"lantus",
    r"levemir",
    r"tresiba",
    r"toujeo",
    r"basaglar",
    r"humulin",
    r"novolin",
)
INSULIN_TEMPORARY = _rx(
    r"sliding scale",
    r"insulin drip",
    r"insulin infusion",
    r"IV insulin",
    r"insulin protocol",
    r"correctional insulin",
)
ORAL_HYPOGLYCEMIC = _rx(
    r"metformin",
    r"glipizide",
    r"glyburide",
    r"glimepiride",
    r"sitagliptin",
    r"linagliptin",
    r"saxagliptin",
    r"alogliptin",
    r"empagliflozin",
    r"dapagliflozin",
    r"canagliflozin",
    r"ertugliflozin",
    r"pioglitazone",
    r"rosiglitazone",
    r"repaglinide",
    r"nateglinide",
    r"januvia",
    r"jardiance",
    r"farxiga",
    r"invokana",
    r"glucophage",
    r"glucotrol",
    r"amaryl",
    r"janumet",
    r"rybelsus",
    r"oral hypoglycemic",
    r"sulfonylurea",
)
INJECTABLE_NON_INSULIN = _rx(
    r"liraglutide",
    r"semaglutide(?! \(rybelsus)",
    r"ozempic",
    r"dulaglutide",
    r"trulicity",
    r"exenatide",
    r"byetta",
    r"bydureon",
    r"tirzepatide",
    r"mounjaro",
    r"victoza",
    r"pramlintide",
    r"symlin",
    r"GLP-1 (?:agonist|receptor agonist)",
)
ANTICOAGULANT = _rx(
    r"apixaban",
    r"eliquis",
    r"rivaroxaban",
    r"xarelto",
    r"warfarin",
    r"coumadin",
    r"dabigatran",
    r"pradaxa",
    r"edoxaban",
    r"savaysa",
    r"long[- ]term anticoagula",
)

MI = _rx(r"myocardial infarction", r"\bMI\b", r"heart attack", r"\bSTEMI\b", r"\bNSTEMI\b")
MI_HISTORICAL = re.compile(
    r"\b(?:old|prior|previous|remote|past|history of|hx of|h/o)\s+(?:an?\s+)?(?:\w+\s+){0,2}"
    r"(?:myocardial infarction|MI|heart attack)\b"
    r"|\b(?:myocardial infarction|MI|heart attack)\b[^.\n]{0,40}\b(?:in (?:19|20)\d\d|\d+\s+(?:years?|months?)\s+ago)",
    re.IGNORECASE,
)
MI_ACUTE = _rx(r"acute (?:myocardial infarction|MI)", r"\bSTEMI\b", r"\bNSTEMI\b", r"evolving (?:MI|infarct)")
STROKE = _rx(r"stroke", r"cerebral infarct", r"\bCVA\b", r"cerebrovascular accident")
STROKE_HISTORICAL = re.compile(
    r"\b(?:history of|hx of|h/o|prior|previous|old|remote|past)\s+(?:an?\s+)?(?:\w+\s+){0,2}"
    r"(?:stroke|cerebral infarct\w*|CVA|cerebrovascular accident)"
    r"|\b(?:stroke|cerebral infarct\w*|CVA)\b[^.\n]{0,40}\b(?:\d+\s+(?:years?|months?)\s+ago|in (?:19|20)\d\d)",
    re.IGNORECASE,
)
NO_RESIDUAL = _rx(r"no residual", r"without residual", r"full recovery", r"fully recovered", r"no deficits?")
DEFICIT = _rx(
    r"hemipare",
    r"hemipleg",
    r"aphasia",
    r"dysphasia",
    r"dysphagia",
    r"dysarthria",
    r"ataxia",
    r"facial droop",
    r"facial weakness",
    r"monopleg",
    r"cognitive deficit",
)

FORMER_SMOKER = _rx(
    r"former (?:smoker|tobacco)",
    r"ex-?smoker",
    r"quit (?:smoking|tobacco)",
    r"(?:stopped|quit) smoking",
    r"history of (?:smoking|tobacco)",
    r"previously smoked",
    r"personal history of (?:tobacco|nicotine)",
    r"former tobacco",
)
CURRENT_SMOKER = _rx(
    r"current(?:ly)? (?:every[- ]day |daily |some[- ]day )?(?:smoker|smokes|smoking)",
    r"(?:every[- ]day|daily|active) (?:cigarette )?smoker",
    r"smokes\b",
    r"\d+(?:\.\d+)?\s*(?:packs?|ppd|cigarettes?) (?:per|a|/) ?day",
    r"\bppd\b",
    r"nicotine dependence",
    r"tobacco dependence",
)
SMOKER = _rx(r"smoker", r"smokes", r"smoking")
CIGARETTE = _rx(r"cigarette", r"\bpack", r"\bppd\b")

PRENATAL = _rx(
    r"prenatal (?:visit|care|exam)",
    r"routine prenatal",
    r"supervision of (?:a )?(?:normal|low[- ]risk)?\s*pregnancy",
    r"normal pregnancy",
    r"uncomplicated pregnancy",
    r"\bOB visit",
    r"antenatal (?:visit|care)",
)
HIGH_RISK_PREG = _rx(
    r"high[- ]risk",
    r"gestational diabetes",
    r"pre-?eclampsia",
    r"placenta previa",
    r"preterm labor",
    r"twin",
    r"multiple gestation",
    r"history of (?:preterm|stillbirth|pre-?eclampsia)",
    r"elderly (?:primigravida|multigravida)",
    r"advanced maternal age",
    r"grand multipar",
)
FIRST_PREGNANCY = re.compile(
    r"\bG1\s*P0\b|\bG1\b|\bfirst pregnancy\b|\bprimigravida\b|\bnulliparous\b", re.IGNORECASE
)
LATER_PREGNANCY = re.compile(
    r"\bG[2-9]\d?\s*P\d|\bG[2-9]\b|\bmultigravida\b|\bsecond pregnancy\b|\bprior (?:vaginal )?delivery\b",
    re.IGNORECASE,
)
WEEKS = re.compile(
    r"\b(\d{1,2})(?:\s*\d/7)?\s*(?:weeks?|wks?)\b(?:\s*(?:\d\s*days?)?)?(?=[^.\n]{0,25}(?:gestation|pregnan|GA\b|EGA|IUP)|\s*(?:gestation|GA\b|EGA))",
    re.IGNORECASE,
)
WEEKS_LOOSE = re.compile(r"\bat\s+(\d{1,2})\s*(?:weeks?|wks?)\b", re.IGNORECASE)
ABORTIVE = _rx(
    r"ectopic",
    r"tubal pregnancy",
    r"molar",
    r"hydatidiform",
    r"abortion",
    r"miscarriage",
    r"products of conception",
    r"blighted ovum",
    r"pregnancy loss",
    r"fetal demise",
)

LATE_ONSET = _rx(r"late[- ]onset", r"late onset")
EARLY_ONSET = _rx(r"early[- ]onset", r"early onset")
DEMENTIA = _rx(r"dementia", r"neurocognitive disorder")
SEVERITY = {
    "A": _rx(
        r"mild (?:dementia|stage|severity)",
        r"dementia[^.\n]{0,20}\bmild\b",
        r"\bmild\b(?=[^.\n]{0,30}dementia)",
    ),
    "B": _rx(
        r"moderate (?:dementia|stage|severity)",
        r"dementia[^.\n]{0,20}\bmoderate\b",
        r"\bmoderate\b(?=[^.\n]{0,30}dementia)",
    ),
    "C": _rx(
        r"severe (?:dementia|stage|severity)",
        r"dementia[^.\n]{0,20}\bsevere\b",
        r"\bsevere\b(?=[^.\n]{0,30}dementia)",
    ),
}
BEHAVIOR = [
    ("11", _rx(r"agitation", r"agitated", r"aggressi", r"combative", r"wandering")),
    ("2", _rx(r"hallucinat", r"delusion", r"psychotic")),
    ("3", _rx(r"depressi", r"apathy", r"mood disturbance")),
    ("4", _rx(r"anxiety", r"anxious")),
]

FLU_IDENTIFIED = _rx(
    r"influenza (?:type )?[AB]\b",
    r"flu (?:type )?[AB]\b",
    r"positive for influenza",
    r"influenza[^.\n]{0,20}positive",
    r"confirmed influenza",
    r"influenza[^.\n]{0,15}confirmed",
    r"\bH1N1\b",
    r"\bH3N2\b",
)
FLU_NOVEL = _rx(r"novel influenza", r"avian", r"\bH5N1\b", r"\bH7N9\b", r"bird flu")
EXACERBATION = _rx(r"exacerbation", r"flare", r"acute(?:ly)? worse", r"decompensat")
STATUS_ASTHMATICUS = _rx(r"status asthmaticus")

OA = _rx(r"osteoarthritis", r"\bOA\b", r"degenerative joint disease", r"\bDJD\b", r"arthrosis")
KNEE = _rx(r"knees?")
HIP = _rx(r"hips?")
BILATERAL = _rx(r"bilateral", r"both (?:knees|hips|sides|feet|eyes)", r"\bB/?L\b")
RIGHT = _rx(r"right", r"\brt\b")
LEFT = _rx(r"left", r"\blt\b")
SCIATICA = _rx(r"sciatica")
LOW_BACK = _rx(r"low(?:er)? back pain", r"lumbago", r"\bLBP\b", r"lumbar pain")

ANNUAL_EXAM = _rx(
    r"annual (?:physical|wellness|preventive|exam)",
    r"routine (?:physical|wellness|annual|health)",
    r"general (?:adult )?medical exam",
    r"wellness (?:exam|visit)",
    r"preventive (?:visit|exam)",
    r"health maintenance exam",
    r"complete physical",
)
PLANNED = _rx(
    r"\bdue\b",
    r"next (?:month|year|week|visit)",
    r"schedul",
    r"upcoming",
    r"overdue",
    r"recommend",
    r"discuss",
    r"consider",
    r"\bwill\b",
    r"refer",
    r"\breturn\b",
    r"declin",
    r"\bplan\b",
    r"in \d+ (?:months?|years?)",
)
ABNORMAL_FINDINGS = _rx(r"with abnormal findings?", r"abnormal finding")
SCREEN_COLON = _rx(
    r"screening colonoscopy",
    r"colonoscopy[^.\n]{0,20}screening",
    r"colorectal cancer screening",
    r"screening for colon cancer",
)

FOOT_SITE = _rx(
    r"foot", r"feet", r"heel", r"toe", r"hallux", r"plantar", r"metatarsal", r"midfoot", r"forefoot"
)
ULCER_DEPTH = [
    ("1", _rx(r"limited to breakdown of (?:the )?skin", r"skin breakdown only", r"partial thickness")),
    ("2", _rx(r"fat layer exposed", r"exposed fat", r"subcutaneous")),
    ("3", _rx(r"necrosis of muscle", r"muscle necrosis")),
    ("4", _rx(r"necrosis of bone", r"bone necrosis")),
    ("5", _rx(r"muscle involvement without (?:evidence of )?necrosis")),
    ("6", _rx(r"bone involvement without (?:evidence of )?necrosis")),
]

FAMILY_SITES = [
    (
        "Z80.0",
        _rx(
            r"colon",
            r"colorectal",
            r"rect(?:al|um)",
            r"bowel",
            r"stomach",
            r"gastric",
            r"pancrea",
            r"esophag",
            r"liver",
        ),
    ),
    ("Z80.1", _rx(r"lung", r"bronch")),
    ("Z80.3", _rx(r"breast")),
    ("Z80.41", _rx(r"ovar")),
    ("Z80.42", _rx(r"prostate")),
    ("Z80.51", _rx(r"kidney", r"renal")),
    ("Z80.52", _rx(r"bladder")),
    ("Z80.6", _rx(r"leukemia")),
    ("Z80.7", _rx(r"lymphoma", r"myeloma")),
    ("Z80.8", _rx(r"melanoma", r"skin cancer", r"brain", r"thyroid")),
]

# Defining documentation for diabetes complication subcategories (E08-E13, 4th/5th character)
DM_COMPLICATION_TERMS = {
    "0": _rx(r"hyperosmola", r"\bHHS\b", r"nonketotic"),
    "1": _rx(r"ketoacidosis", r"\bDKA\b", r"ketosis"),
    "2": _rx(
        r"kidney",
        r"renal",
        r"nephropath",
        r"\bCKD\b",
        r"\bESRD\b",
        r"glomerul",
        r"albuminuria",
        r"proteinuria",
        r"nephrosis",
    ),
    "21": _rx(r"nephropath", r"nephrosis", r"glomerul", r"kimmelstiel", r"intercapillary"),
    "22": _rx(r"chronic kidney", r"\bCKD\b", r"\bESRD\b", r"end[- ]stage renal", r"chronic renal"),
    "620": _rx(r"dermatitis", r"necrobiosis"),
    "621": _rx(
        r"foot ulcer",
        r"ulcer[^.\n]{0,40}(?:foot|heel|toe|hallux|plantar|metatarsal|midfoot)",
        r"(?:foot|heel|toe|hallux|plantar)[^.\n]{0,30}ulcer",
    ),
    "3": _rx(r"retinopath", r"macular", r"cataract", r"ophthalm", r"\beye"),
    "4": _rx(
        r"neuropath",
        r"polyneuropath",
        r"mononeuropath",
        r"amyotroph",
        r"gastroparesis",
        r"autonomic",
        r"neuralgia",
        r"radiculopath",
    ),
    "5": _rx(
        r"angiopath",
        r"peripheral arter",
        r"peripheral vascular",
        r"\bPAD\b",
        r"\bPVD\b",
        r"gangrene",
        r"claudication",
    ),
    "61": _rx(r"arthropath", r"charcot"),
    "62": _rx(r"ulcer", r"dermatitis", r"necrobiosis", r"skin complication"),
    "63": _rx(r"periodont", r"oral complication"),
    "64": _rx(r"hypoglyc", r"low (?:blood )?(?:sugar|glucose)"),
    "65": _rx(
        r"hyperglyc", r"uncontrolled", r"poorly controlled", r"out of control", r"inadequately controlled"
    ),
    "A": _rx(r"presymptomatic", r"stage [12] type 1", r"autoantibod"),
}

# Anatomic region (by S-chapter block) that an injury code requires in the documentation
INJURY_REGION = {
    "S0": _rx(
        r"head",
        r"skull",
        r"scalp",
        r"face",
        r"facial",
        r"eye",
        r"orbit",
        r"nose",
        r"nasal",
        r"\bears?\b",
        r"cheek",
        r"jaw",
        r"mandib",
        r"maxill",
        r"\blips?\b",
        r"mouth",
        r"tooth",
        r"teeth",
        r"forehead",
        r"brain",
        r"concussion",
        r"intracranial",
        r"cranial",
        r"zygom",
        r"eyelid",
        r"chin",
    ),
    "S1": _rx(r"neck", r"cervical", r"throat", r"larynx", r"trachea"),
    "S2": _rx(
        r"chest",
        r"thora",
        r"\bribs?\b",
        r"stern",
        r"breast",
        r"lung",
        r"pneumothorax",
        r"hemothorax",
        r"upper back",
        r"thoracic spine",
    ),
    "S3": _rx(
        r"abdom",
        r"lower back",
        r"lumbar",
        r"pelvi",
        r"sacr",
        r"coccyx",
        r"ilium",
        r"iliac",
        r"pubic",
        r"acetabul",
        r"buttock",
        r"genital",
        r"penis",
        r"scrot",
        r"vagina",
        r"vulva",
        r"kidney",
        r"spleen",
        r"liver",
        r"bladder",
        r"flank",
        r"groin",
    ),
    "S4": _rx(r"shoulder", r"upper arm", r"humer", r"clavic", r"scapul", r"axill", r"rotator"),
    "S5": _rx(
        r"elbow",
        r"forearm",
        r"radius",
        r"radial",
        r"ulna",
        r"olecranon",
        r"colles",
        r"smith'?s? fracture",
        r"monteggia",
        r"galeazzi",
    ),
    "S6": _rx(
        r"wrist",
        r"hand",
        r"finger",
        r"thumb",
        r"carpal",
        r"metacarpal",
        r"scaphoid",
        r"palm",
        r"digit",
        r"\bnail",
    ),
    "S7": _rx(r"\bhip", r"thigh", r"femur", r"femoral", r"trochant"),
    "S8": _rx(
        r"knee",
        r"lower leg",
        r"tibia",
        r"fibula",
        r"patella",
        r"\bshin",
        r"calf",
        r"\bleg\b",
        r"malleol",
        r"ankle",
        r"achilles",
    ),
    "S9": _rx(
        r"ankle",
        r"\bfoot",
        r"\bfeet",
        r"\btoes?\b",
        r"metatars",
        r"calcane",
        r"heel",
        r"tars",
        r"talus",
        r"hallux",
        r"malleol",
    ),
}
SEQUELA = _rx(r"sequela", r"late effect", r"residual", r"\bscar")
SUBSEQUENT = _rx(
    r"subsequent encounter",
    r"routine healing",
    r"follow[- ]up (?:of|for) (?:the )?fracture",
    r"cast (?:check|change)",
    r"healing fracture",
)
PNEUMONIA = _rx(r"pneumonia", r"\bCAP\b", r"\bpneumonitis")
ANGINA = _rx(r"angina", r"anginal")
NO_ANGINA = _rx(r"no angina", r"without angina", r"denies angina", r"angina[- ]free")
CORONARY_STENT = _rx(
    r"(?:coronary|cardiac|LAD|RCA|circumflex|drug[- ]eluting|bare[- ]metal)\s+(?:artery\s+)?stents?",
    r"stents? (?:to|in) the (?:LAD|RCA|circumflex|left anterior|right coronary|coronary)",
    r"PCI (?:and|with) stent",
    r"status post (?:coronary )?stent",
    r"s/p (?:coronary )?stent",
)
STENT_PREVIOUS = _rx(
    r"status post",
    r"s/p",
    r"history of",
    r"previous(?:ly)?",
    r"prior",
    r"(?:19|20)\d\d",
    r"\d+\s+(?:years?|months?)\s+ago",
    r"old",
    r"existing",
    r"patent stent",
)
STENT_NOW = _rx(
    r"was placed",
    r"were placed",
    r"placed today",
    r"deployed",
    r"inserted",
    r"implanted",
    r"underwent (?:PCI|stent)",
    r"this admission",
)
SEPSIS_ORGANISM = {
    "A40.": _rx(r"strep"),
    "A41.0": _rx(r"staph", r"\bMRSA\b", r"\bMSSA\b"),
    "A41.1": _rx(r"staph"),
    "A41.2": _rx(r"staph"),
    "A41.3": _rx(r"haemophilus", r"h\.? influenzae"),
    "A41.4": _rx(r"anaerob", r"bacteroides", r"clostrid"),
    "A41.5": _rx(
        r"gram[- ]negative",
        r"e\.? ?coli",
        r"escherichia",
        r"pseudomonas",
        r"serratia",
        r"klebsiella",
        r"enterobacter",
        r"proteus",
        r"acinetobacter",
    ),
}
HYPOGLYCEMIA = _rx(r"hypoglyc", r"low (?:blood )?(?:sugar|glucose)")
COMA = _rx(r"\bcoma\b", r"unresponsive", r"loss of consciousness")
BACTERIAL = _rx(
    r"bacteri",
    r"staph",
    r"strep",
    r"klebsiella",
    r"pseudomonas",
    r"e\.? ?coli",
    r"\bMRSA\b",
    r"\bMSSA\b",
    r"gram[- ](?:positive|negative)",
    r"haemophilus",
    r"h\.? influenzae",
    r"legionella",
    r"mycoplasma",
    r"chlamydi",
    r"anaerob",
    r"aspiration",
    r"pneumococc",
)

# Signs/symptoms routinely associated with a definitive diagnosis (Guideline I.B.5)
INTEGRAL_SYMPTOMS = [
    (("N39.0", "N30.", "N10", "N12"), ("R30.", "R35.0", "R39.15", "R39.16", "R82.")),
    (("K35.", "K36", "K37"), ("R10.", "R11.", "R50.9", "D72.829")),
    (
        ("J18.", "J15.", "J13", "J14", "J12.", "J10.", "J11.", "J44.", "J45.", "J20.", "J06."),
        ("R05.", "R06.0", "R06.2", "R50.9"),
    ),
    (("I50.", "I11.0", "I13.0", "I13.2"), ("R06.0", "R60.", "R06.00")),
    (("M17.", "M16."), ("M25.56", "M25.55")),
    (("M54.4",), ("M54.5", "M54.3")),
    (("K21.",), ("R12",)),
    (("I21.", "I22.", "I20."), ("R07.",)),
]


# A qualifier in a code title must be documented (affirmed) before the code can be reported.
# (description pattern, required documentation) - also an anti-upcoding safeguard.
QUALIFIER_SUPPORT: list[tuple[re.Pattern[str], re.Pattern[str]]] = [
    (re.compile(r"\binflammatory\b", re.I), _rx(r"inflammatory")),
    (re.compile(r"\bin situ\b", re.I), _rx(r"in situ", r"\bDCIS\b", r"\bLCIS\b", r"\bCIS\b")),
    (re.compile(r"^secondary malignant", re.I), _rx(r"metasta", r"secondary (?:malignan|cancer|tumou?r)")),
    (re.compile(r"\bwith coma\b", re.I), _rx(r"\bcoma\b", r"comatose")),
    (re.compile(r"\bwith perforation\b", re.I), _rx(r"perforat", r"ruptur")),
    (re.compile(r"\bwith gangrene\b", re.I), _rx(r"gangren")),
    (re.compile(r"\bwith (?:localized |generalized )?peritonitis\b", re.I), _rx(r"peritonitis")),
    (re.compile(r"\bwith abscess\b", re.I), _rx(r"abscess")),
    (
        re.compile(r"\bwith (?:hemorrhage|bleeding)\b", re.I),
        _rx(r"hemorrhag", r"bleed", r"melena", r"hematemesis"),
    ),
    (re.compile(r"\bwith obstruction\b", re.I), _rx(r"obstruct")),
    (re.compile(r"\bwith psychotic", re.I), _rx(r"psychotic", r"hallucinat", r"delusion")),
    (re.compile(r"\bwith septic shock\b", re.I), _rx(r"septic shock")),
    (re.compile(r"\bpresymptomatic\b", re.I), _rx(r"presymptomatic")),
    (re.compile(r"\bstatus asthmaticus\b", re.I), _rx(r"status asthmaticus")),
    (re.compile(r"\bwith \(acute\) exacerbation\b", re.I), _rx(r"exacerbat", r"flare")),
    (re.compile(r"\bwith (?:unspecified type of |other |specified )?pneumonia\b", re.I), _rx(r"pneumonia")),
    (re.compile(r"\brecurrent\b", re.I), _rx(r"recurren")),
    (re.compile(r"\bwith macular edema\b", re.I), _rx(r"macular edema", r"\bDME\b", r"\bCSME\b")),
    (
        re.compile(r"\bunstable angina\b", re.I),
        _rx(r"unstable angina", r"crescendo angina", r"accelerated angina"),
    ),
    (re.compile(r"\bwith documented spasm\b", re.I), _rx(r"spasm", r"vasospas", r"prinzmetal")),
    (re.compile(r"\brefractory angina\b", re.I), _rx(r"refractory")),
    (re.compile(r"\bwith agitation\b", re.I), _rx(r"agitat", r"aggressi", r"combative")),
    (
        re.compile(r"\bmorbid \(severe\) obesity\b", re.I),
        _rx(r"morbid", r"severe obesity", r"class (?:3|III) obesity"),
    ),
    # high-risk (CC/MCC) conditions: never reported unless the provider documents them
    (re.compile(r"\brespiratory failure\b", re.I), _rx(r"respiratory failure")),
    (re.compile(r"\bsepsis\b|\bseptic\b", re.I), _rx(r"sepsis", r"septic", r"septicemia")),
    (
        re.compile(r"\bacute kidney failure\b", re.I),
        _rx(r"acute kidney", r"acute renal", r"\bAKI\b", r"\bARF\b"),
    ),
    (re.compile(r"\bencephalopathy\b", re.I), _rx(r"encephalopath")),
    (re.compile(r"\bmalnutrition\b|\bcachexia\b", re.I), _rx(r"malnutrition", r"malnourish", r"cachexi")),
    (re.compile(r"^shock\b|\bcardiogenic shock\b|\bhypovolemic shock\b", re.I), _rx(r"\bshock\b")),
    (
        re.compile(r"\bsubsequent (?:non-)?ST elevation|\bsubsequent .*myocardial infarction", re.I),
        _rx(
            r"subsequent (?:MI|myocardial|infarct)",
            r"reinfarction",
            r"recurrent (?:MI|myocardial infarction)",
        ),
    ),
    # causes stated in the title must be documented
    (re.compile(r"\bsarcoid", re.I), _rx(r"sarcoid")),
    (re.compile(r"\bobstructive\b", re.I), _rx(r"obstruct", r"\bCOPD\b", r"\bOSA\b", r"\bOSAS\b")),
    (re.compile(r"\btuberculous\b", re.I), _rx(r"tubercul", r"\bTB\b")),
    (re.compile(r"\bsyphilitic\b", re.I), _rx(r"syphil")),
    (re.compile(r"\bgonococcal\b", re.I), _rx(r"gonorr", r"gonococ")),
    (re.compile(r"\blupus\b", re.I), _rx(r"lupus", r"\bSLE\b")),
    (re.compile(r"\bgouty\b|\bgout\b", re.I), _rx(r"gout")),
    (re.compile(r"\brheumatoid\b", re.I), _rx(r"rheumatoid")),
    (re.compile(r"\balcoholic\b|alcohol[- ]induced", re.I), _rx(r"alcohol", r"\bETOH\b", r"EtOH")),
    (
        re.compile(r"\bdrug[- ]induced\b", re.I),
        _rx(r"drug[- ]induced", r"medication[- ]induced", r"induced by"),
    ),
    (
        re.compile(r"\bpostprocedural\b|\bpostoperative\b|following (?:a )?procedure", re.I),
        _rx(
            r"post-?op",
            r"postoperative",
            r"postprocedural",
            r"after (?:surgery|the procedure)",
            r"following (?:surgery|procedure)",
        ),
    ),
    (re.compile(r"\bcongenital\b", re.I), _rx(r"congenital", r"since birth")),
    (re.compile(r"\bnewborn\b|\bneonatal\b|\bperinatal\b", re.I), _rx(r"newborn", r"neonat", r"perinatal")),
    (re.compile(r"\bpuerperal\b|\bpostpartum\b", re.I), _rx(r"postpartum", r"puerper", r"post-?partum")),
    (re.compile(r"vomiting (?:in|of) pregnancy|hyperemesis", re.I), _rx(r"vomit", r"hyperemesis")),
    (
        re.compile(r"\bstenosis of coronary (?:artery )?stent|\bcoronary (?:artery )?stent stenosis", re.I),
        _rx(r"in-?stent (?:re)?stenosis", r"stent (?:re)?stenosis", r"restenosis"),
    ),
]
# Organisms named in chapter 1 (A00-B99) titles must be documented.
ORGANISMS: list[tuple[re.Pattern[str], re.Pattern[str]]] = [
    (re.compile(p, re.I), _rx(*alts))
    for p, alts in (
        (r"salmonell", [r"salmonell"]),
        (r"shigell", [r"shigell"]),
        (r"escherichia|e\. coli", [r"e\.? ?coli", r"escherichia"]),
        (r"campylobacter", [r"campylobacter"]),
        (r"staphylococ", [r"staph", r"\bMRSA\b", r"\bMSSA\b"]),
        (r"streptococ", [r"strep"]),
        (r"pneumococ", [r"pneumococ", r"strep(?:tococcus)? pneumoniae"]),
        (r"klebsiella", [r"klebsiella"]),
        (r"pseudomonas", [r"pseudomonas"]),
        (r"clostridi", [r"clostrid", r"c\.? diff"]),
        (r"candid", [r"candid", r"yeast", r"thrush"]),
        (r"herpes|herpesvir", [r"herpe", r"\bHSV\b"]),
        (r"zoster|varicella", [r"zoster", r"shingles", r"varicella", r"chickenpox"]),
        (r"tubercul", [r"tubercul", r"\bTB\b"]),
        (r"chlamydia", [r"chlamyd"]),
        (r"gonococ", [r"gonorr", r"gonococ"]),
        (r"syphilis", [r"syphil"]),
        (r"cytomegalo", [r"cytomegalo", r"\bCMV\b"]),
        (r"aspergill", [r"aspergill"]),
        (r"mycoplasma", [r"mycoplasma"]),
        (r"legionell", [r"legionell"]),
        (r"haemophilus|influenzae", [r"haemophilus", r"h\.? influenzae"]),
        (r"rotavir", [r"rotavir"]),
        (r"norovir|norwalk", [r"norovir", r"norwalk"]),
        (r"hepatitis", [r"hepatitis", r"\bHBV\b", r"\bHCV\b"]),
        (r"\bhuman immunodeficiency virus\b", [r"\bHIV\b", r"human immunodeficiency"]),
        (r"covid|coronavirus", [r"covid", r"sars-?cov", r"coronavirus"]),
        (r"giardi", [r"giardi"]),
        (r"lyme|borrelia", [r"lyme", r"borreli"]),
        (r"helicobacter", [r"helicobacter", r"h\.? pylori"]),
    )
]
MALIGNANCY = _rx(
    r"cancer",
    r"carcinoma",
    r"malignan",
    r"lymphoma",
    r"leuk[ae]mia",
    r"melanoma",
    r"sarcoma",
    r"myeloma",
    r"neoplasm",
    r"tumou?r",
    r"metasta",
    r"\bCA\b",
    r"glioma",
    r"blastoma",
    r"mesothelioma",
)
TYPE1 = _rx(
    r"type 1", r"type I\b", r"\bT1DM\b", r"\bDM1\b", r"\bDM 1\b", r"juvenile (?:onset )?diabetes", r"\bIDDM\b"
)
TYPE2 = _rx(
    r"type 2", r"type II\b", r"\bT2DM\b", r"\bDM2\b", r"\bDM 2\b", r"\bNIDDM\b", r"adult[- ]onset diabetes"
)
MSK_SITE = {"M16": HIP, "M17": KNEE, "M18": _rx(r"thumb", r"carpometacarpal", r"\bCMC\b")}
AROMATASE = _rx(
    r"anastrozole", r"arimidex", r"letrozole", r"femara", r"exemestane", r"aromasin", r"aromatase inhibitor"
)
SERM = _rx(r"tamoxifen", r"raloxifene", r"evista", r"toremifene")


# Body site named in a chapter 13 (M00-M99) title must be documented (wrong-site safety check).
MSK_TITLE_SITES: list[tuple[re.Pattern[str], re.Pattern[str]]] = [
    (re.compile(r"\bshoulder\b", re.I), _rx(r"shoulder", r"rotator", r"glenohumeral", r"acromio")),
    (re.compile(r"\bupper arm\b", re.I), _rx(r"upper arm", r"humer", r"biceps", r"triceps")),
    (re.compile(r"\belbow\b", re.I), _rx(r"elbow", r"olecranon", r"epicondyl")),
    (re.compile(r"\bforearm\b", re.I), _rx(r"forearm", r"radius", r"ulna")),
    (re.compile(r"\bwrist\b", re.I), _rx(r"wrist", r"carpal")),
    (
        re.compile(r"\bhand\b", re.I),
        _rx(
            r"\bhands?\b", r"finger", r"thumb", r"\bMCP\b", r"\bPIP\b", r"\bDIP\b", r"knuckle", r"metacarpal"
        ),
    ),
    (re.compile(r"\bhip\b", re.I), _rx(r"\bhips?\b", r"femoral head", r"acetabul")),
    (re.compile(r"\bthigh\b", re.I), _rx(r"thigh", r"femur", r"quadriceps", r"hamstring")),
    (re.compile(r"\bknee\b", re.I), _rx(r"knee", r"patell", r"menisc")),
    (re.compile(r"\blower leg\b", re.I), _rx(r"lower leg", r"tibia", r"fibula", r"\bshin", r"\bcalf")),
    (
        re.compile(r"\bankle and foot\b|\bankle and toes\b|\bfoot\b|\btoes?\b", re.I),
        _rx(
            r"ankle",
            r"\bfoot",
            r"\bfeet",
            r"\btoes?\b",
            r"heel",
            r"metatars",
            r"hallux",
            r"\bMTP\b",
            r"bunion",
            r"plantar",
            r"calcane",
        ),
    ),
    (re.compile(r"\bcervical region\b", re.I), _rx(r"cervical", r"neck")),
    (re.compile(r"\bthoracic region\b", re.I), _rx(r"thoracic", r"mid back", r"upper back")),
    (re.compile(r"\blumbar region\b", re.I), _rx(r"lumbar", r"low(?:er)? back", r"lumbago", r"\bLBP\b")),
]


def documentation_gap(code: str, description: str, doc: Doc) -> str | None:
    """Why the documentation does NOT support this ICD-10-CM code (None = supported / not checked)."""
    n = normalize_code(code, ICD)
    desc = description or ""
    if n[:3] in ("E08", "E09", "E10", "E11", "E13") and len(n) > 4:
        tail = n[4:]
        if tail[0] not in "89":
            pat = next(
                (
                    DM_COMPLICATION_TERMS[k]
                    for k in (tail[:3], tail[:2], tail[0])
                    if k in DM_COMPLICATION_TERMS
                ),
                None,
            )
            if pat is not None and not doc.has(pat):
                return f"'{desc}' requires a documented complication; none found in the note"
        if n[:3] == "E10" and doc.has(TYPE2) and not doc.has(TYPE1):
            return "type 2 diabetes is documented, not type 1"
        if n[:3] == "E11" and doc.has(TYPE1) and not doc.has(TYPE2):
            return "type 1 diabetes is documented, not type 2"
    region = INJURY_REGION.get(n[:2]) if n[:1] == "S" else None
    if region is not None and not doc.raw(region):
        return f"anatomic site of {n} ({desc}) is not documented"
    if n[:3] in ("O00", "O01", "O02", "O03", "O04", "O07", "O08") and not doc.has(ABORTIVE):
        return "ectopic/molar pregnancy or abortion/miscarriage is not documented"
    if n[:3] in MSK_SITE and not doc.raw(MSK_SITE[n[:3]]):
        return f"the joint in {n} ({desc}) is not documented"
    if n[:1] == "M" and not re.search(r"multiple sites|unspecified site|site unspecified", desc, re.I):
        for title, need in MSK_TITLE_SITES:
            if title.search(desc) and not doc.raw(need):
                return f"the body site in '{desc}' is not documented"
    if n[:1] == "C" and n[:3] < "C97" and not doc.has(MALIGNANCY):
        return "no malignancy is documented"
    if "I10" <= n[:3] <= "I16" and not [
        m for m in doc.affirmed(HTN) if not NOT_ESSENTIAL_HTN.search(doc.sentence(m.start))
    ]:
        return "hypertension is not documented as a current diagnosis"
    if n[:1] in "AB":
        for title, need in ORGANISMS:
            if title.search(desc) and not doc.has(need):
                return f"the organism in '{desc}' is not documented"
    if (
        re.search(r"\bchronic\b", desc, re.I)
        and not re.search(r"\bacute\b", desc, re.I)
        and doc.has(_rx(r"acute"))
        and not doc.has(_rx(r"chronic", r"\bCKD\b", r"\bCOPD\b", r"long[- ]standing"))
    ):
        return f"'{desc}' is chronic but the documentation describes an acute condition"
    if (
        re.search(r"\bacute\b", desc, re.I)
        and not re.search(r"\bchronic\b", desc, re.I)
        and doc.has(_rx(r"chronic"))
        and not doc.has(_rx(r"acute", r"exacerbation", r"flare"))
    ):
        return f"'{desc}' is acute but the documentation describes a chronic condition"
    for title, need in QUALIFIER_SUPPORT:
        if title.search(desc) and not doc.has(need):
            label = title.pattern.replace("\\b", "").lstrip("^")
            return f"'{label}' in '{desc}' is not documented"
    return None


# --------------------------------------------------------------------------- engine
class GuidelineRules:
    def __init__(self, snapshot: KBSnapshot | None):
        self.snap = snapshot

    # ---- code-list helpers
    def _billable(self, code: str) -> bool:
        if self.snap is None:
            return False
        info = self.snap.get(code)
        return bool(info and info.billable)

    @staticmethod
    def _n(code: str) -> str:
        return normalize_code(code, ICD)

    def apply(
        self,
        codes: list[LLMCode],
        not_coded: list[NotCoded],
        text: str,
        sections: list[Section],
        encounter_type: str,
        patient_age: int | None = None,
    ) -> list[RuleChange]:
        if self.snap is None or not text:
            return []
        self.codes = codes
        self.not_coded = not_coded
        self.doc = Doc(text, sections, encounter_type)
        self.age = patient_age
        self.changes: list[RuleChange] = []
        for step in (
            self._documentation_support,
            self._injury_seventh_character,
            self._ensure_diabetes,
            self._historical_mi,
            self._historical_stroke,
            self._hypertension_combinations,
            self._diabetes_ckd,
            self._diabetes_glycemia,
            self._diabetic_retinopathy,
            self._angina_type,
            self._influenza_manifestation,
            self._antineoplastic_therapy,
            self._diabetes_drug_therapy,
            self._coronary_stent_status,
            self._anticoagulant_therapy,
            self._diabetic_foot_ulcer,
            self._tobacco,
            self._pregnancy_supervision,
            self._alzheimer_dementia,
            self._influenza_identified,
            self._asthma_copd_exacerbation,
            self._osteoarthritis_site,
            self._lumbago_sciatica,
            self._family_history_site,
            self._encounter_reasons,
            self._integral_symptoms,
            self._cleanup_conflicts,
        ):
            try:
                step()
            except Exception as exc:  # a rule must never break coding  # noqa: BLE001
                self.not_coded.append(
                    NotCoded(entity=step.__name__, reason=f"guideline rule skipped: {exc}"[:300])
                )
        return self.changes

    def icd(self) -> list[LLMCode]:
        """ICD-10-CM codes that exist in the knowledge base. Unknown/invalid codes are left untouched for the
        validator to reject (rules never reason from, or silently drop, a hallucinated code)."""
        return [c for c in self.codes if c.code_system == ICD and self.snap.get(c.code) is not None]

    def find(self, *prefixes: str) -> list[LLMCode]:
        out = []
        for c in self.icd():
            n = self._n(c.code)
            if any(n.startswith(p) for p in prefixes):
                out.append(c)
        return out

    def has_code(self, *prefixes: str) -> bool:
        return bool(self.find(*prefixes))

    def remove(self, c: LLMCode, rule: str, guideline: str, reason: str) -> None:
        if c in self.codes:
            self.codes.remove(c)
            self.not_coded.append(
                NotCoded(
                    entity=f"{c.code} {c.description}".strip()[:300],
                    reason=f"Removed by coder rule ({guideline}): {reason}"[:500],
                )
            )
            self.changes.append(RuleChange(rule, guideline, removed=[self._n(c.code)], note=reason))

    def add(
        self,
        code: str,
        rule: str,
        guideline: str,
        reason: str,
        evidence: list[str],
        entity: str = "",
        replaces: LLMCode | None = None,
        position: int | None = None,
    ) -> bool:
        code = self._n(code)
        if not self._billable(code):
            self.not_coded.append(
                NotCoded(
                    entity=entity or code,
                    reason=f"Coder rule ({guideline}) suggests {code}, not billable in KB; review",
                )
            )
            return False
        if any(self._n(c.code) == code for c in self.icd()):
            if replaces is not None and self._n(replaces.code) != code:
                self.remove(replaces, rule, guideline, reason)
            return False
        info = self.snap.get(code)
        idx = position
        if replaces is not None and replaces in self.codes:
            idx = self.codes.index(replaces)
            self.codes.remove(replaces)
            self.not_coded.append(
                NotCoded(
                    entity=f"{replaces.code} {replaces.description}".strip()[:300],
                    reason=f"Replaced by {code} ({guideline}): {reason}"[:500],
                )
            )
        new = LLMCode(
            code=code,
            code_system=ICD,
            description=info.description if info else "",
            entity=(entity or (replaces.entity if replaces else ""))[:300],
            evidence=[e[:500] for e in evidence[:3]] or ([*replaces.evidence] if replaces else []),
            rationale=f"[{RULES_VERSION} | {guideline}] {reason}"[:1000],
            confidence=0.9,
            from_candidates=True,
        )
        if idx is None:
            self.codes.append(new)
        else:
            self.codes.insert(min(idx, len(self.codes)), new)
        self.changes.append(
            RuleChange(
                rule,
                guideline,
                removed=[self._n(replaces.code)] if replaces else [],
                added=[code],
                note=reason,
            )
        )
        return True

    def replace(
        self,
        old: LLMCode,
        new_code: str,
        rule: str,
        guideline: str,
        reason: str,
        evidence: list[str] | None = None,
    ) -> bool:
        if self._n(old.code) == self._n(new_code):
            return False
        return self.add(
            new_code, rule, guideline, reason, evidence or list(old.evidence), old.entity, replaces=old
        )

    # ---- laterality helper: side words in the sentences of the given mentions
    def _side(self, mentions: list[Entity]) -> str | None:
        sides = set()
        for m in mentions:
            s = self.doc.sentence(m.start)
            if BILATERAL.search(s):
                sides.add("B")
            if RIGHT.search(s):
                sides.add("R")
            if LEFT.search(s):
                sides.add("L")
        if "B" in sides or {"R", "L"} <= sides:
            return "B"
        return next(iter(sides)) if sides else None

    def _joint_side(self, mentions: list[Entity], joint: str) -> str | None:
        """Side written next to the joint word ('left knee', 'knee, right', 'both knees'), so 'OA of the left knee,
        right knee normal' stays left."""
        sides = set()
        j = rf"{joint}s?"
        for m in mentions:
            s = self.doc.sentence(m.start)
            if re.search(
                rf"\b(?:bilateral|both)\s+(?:\w+\s+){{0,4}}{j}\b|\b{j}\b[^.;,\n]{{0,12}}\bbilateral", s, re.I
            ):
                sides.add("B")
            for side, word in (("R", "right"), ("L", "left")):
                pattern = rf"\b{word}\s+(?:\w+\s+){{0,2}}{j}\b|\b{j}\b,?\s+{word}\b(?!\s+(?:\w+\s+)?{j}\b)"
                for hit in re.finditer(pattern, s, re.I):
                    rest = s[hit.end() : hit.end() + 25].lower()
                    if not re.match(
                        r"\s*(?:exam\s+)?(?:is\s+|was\s+)?(?:normal|unremarkable|negative)", rest
                    ):
                        sides.add(side)
        if "B" in sides or {"R", "L"} <= sides:
            return "B"
        return next(iter(sides)) if sides else None

    # ================================================================ rules
    def _documentation_support(self) -> None:
        """Remove codes whose defining specificity is not documented (anti-hallucination)."""
        g_dm = "I.A.15 / I.C.4.a (diabetes complication must be documented)"
        for c in self.find("E08.", "E09.", "E10.", "E11.", "E13."):
            n = self._n(c.code)
            tail = n[4:]  # after 'E11.'
            if not tail or tail[0] in "89":
                continue
            pat = next(
                (
                    DM_COMPLICATION_TERMS[k]
                    for k in (tail[:3], tail[:2], tail[0])
                    if k in DM_COMPLICATION_TERMS
                ),
                None,
            )
            if pat is not None and not self.doc.has(pat):
                reason = f"'{c.description}' requires a documented complication; none found in the note"
                others = [o for o in self.find(n[:4]) if o is not c]
                if others or not self.replace(c, n[:4] + "9", "diabetes_complication_support", g_dm, reason):
                    self.remove(c, "diabetes_complication_support", g_dm, reason)
        g_doc = "I.A.19 / I.B.14 (code assignment is based on the provider's documentation)"
        for c in list(self.icd()):
            n = self._n(c.code)
            if n[:3] in ("E08", "E09", "E10", "E11", "E13", "J15", "A40", "A41") or n[:1] == "S":
                continue  # handled below with a specific fallback
            info = self.snap.get(n)
            gap = documentation_gap(n, info.description if info else c.description, self.doc)
            if gap:
                self.remove(c, "documentation_support", g_doc, gap)
        for c in self.find("E10.", "E11."):
            n = self._n(c.code)
            if (n[:3] == "E10" and self.doc.has(TYPE2) and not self.doc.has(TYPE1)) or (
                n[:3] == "E11" and self.doc.has(TYPE1) and not self.doc.has(TYPE2)
            ):
                other = ("E11" if n[:3] == "E10" else "E10") + n[3:]
                reason = "diabetes type in the code contradicts the documented type"
                if not self.replace(
                    c, other, "diabetes_type", "I.C.4.a.1 (type of diabetes as documented)", reason
                ):
                    self.remove(c, "diabetes_type", "I.C.4.a.1", reason)
        g_inj = "I.C.19 (injury code must match the documented anatomic site)"
        for c in self.find("S"):
            n = self._n(c.code)
            region = INJURY_REGION.get(n[:2])
            if region is not None and not self.doc.raw(region):
                self.remove(
                    c,
                    "injury_site_support",
                    g_inj,
                    f"anatomic site of {n} ({c.description}) is not documented",
                )
        for c in self.find("J15."):
            if not self.doc.has(BACTERIAL):
                g_pn = "Index: Pneumonia (NOS / community acquired) J18.9; bacterial J15.- needs documented cause"
                reason = "bacterial pneumonia requires a documented bacterial cause or organism"
                if not (
                    self.doc.has(PNEUMONIA)
                    and not self.has_code("J10.0", "J11.0", "J12.", "J13", "J14", "J18.")
                    and self.replace(
                        c, "J18.9", "bacterial_pneumonia_support", g_pn, reason, self.doc.evidence(PNEUMONIA)
                    )
                ):
                    self.remove(c, "bacterial_pneumonia_support", g_pn, reason)
        for c in self.find("I25.11", "I20."):
            if self.doc.raw(NO_ANGINA) or not self.doc.has(ANGINA, current=True):
                reason = "angina is not documented as present"
                if not (
                    self.find("I25.11")
                    and c in self.find("I25.11")
                    and self.replace(
                        c,
                        "I25.10",
                        "angina_support",
                        "Index: Disease, coronary, without angina I25.10",
                        reason,
                    )
                ):
                    self.remove(c, "angina_support", "angina must be documented", reason)
        for prefix, pat in SEPSIS_ORGANISM.items():
            for c in self.find(prefix):
                if not self.doc.has(pat):
                    reason = "the organism in this sepsis code is not documented"
                    if not self.replace(
                        c,
                        "A41.9",
                        "sepsis_organism_support",
                        "I.C.1.d (sepsis: code the documented organism; unspecified A41.9)",
                        reason,
                    ):
                        self.remove(c, "sepsis_organism_support", "I.C.1.d", reason)
        for c in self.find("O00", "O01", "O02", "O03", "O04", "O07", "O08"):
            if not self.doc.has(ABORTIVE):
                self.remove(
                    c,
                    "abortive_outcome_support",
                    "I.C.15.k (pregnancy with abortive outcome)",
                    "ectopic/molar pregnancy or abortion/miscarriage is not documented",
                )

    def _injury_seventh_character(self) -> None:
        g = "I.C.19.a (7th character: A initial, D subsequent, S sequela)"
        for c in self.find("S", "T"):
            n = self._n(c.code)
            if len(n) < 8 or n[-1] != "S" or self.doc.raw(SEQUELA):
                continue
            new = n[:-1] + ("D" if self.doc.raw(SUBSEQUENT) else "A")
            if not self.replace(
                c,
                new,
                "injury_seventh_character",
                g,
                "sequela (S) is not documented; active treatment of the injury",
            ):
                self.remove(c, "injury_seventh_character", g, "sequela (S) is not documented")

    def _historical_mi(self) -> None:
        g = "I.C.9.e.1 (acute MI is coded for 4 weeks; afterwards I25.2 Old myocardial infarction)"
        acute = self.find("I21.", "I22.")
        if not acute or self.doc.has(MI_ACUTE, current=True):
            return
        current_mi = [
            m
            for m in self.doc.affirmed(MI, current=True)
            if not MI_HISTORICAL.search(self.doc.sentence(m.start))
        ]
        if current_mi and not self.doc.raw(MI_HISTORICAL):
            return
        if not self.doc.raw(MI_HISTORICAL) and not [m for m in self.doc.mentions(MI) if m.historical]:
            return
        ev = self.doc.evidence(MI_HISTORICAL, MI)
        for c in acute:
            if not self.replace(
                c, "I25.2", "historical_mi", g, "myocardial infarction is documented as historical", ev
            ):
                self.remove(c, "historical_mi", g, "myocardial infarction is documented as historical")

    def _historical_stroke(self) -> None:
        g = "I.C.9.d (sequelae I69 / history Z86.73, not acute I63)"
        acute = self.find("I63.")
        if not acute or not self.doc.raw(STROKE_HISTORICAL):
            return
        if [
            m
            for m in self.doc.affirmed(STROKE, current=True)
            if not STROKE_HISTORICAL.search(self.doc.sentence(m.start))
        ]:
            return
        has_deficit = self.doc.has(DEFICIT, current=True) and not self.doc.raw(NO_RESIDUAL)
        for c in acute:
            if has_deficit:
                if self.has_code("I69."):
                    self.remove(
                        c, "historical_stroke", g, "stroke is historical; residual deficit coded with I69"
                    )
                else:
                    self.remove(
                        c,
                        "historical_stroke",
                        g,
                        "stroke is historical with residual deficit: assign I69.3- (sequela) by deficit and side",
                    )
            elif not self.replace(
                c,
                "Z86.73",
                "historical_stroke",
                g,
                "stroke is historical without residual deficits",
                self.doc.evidence(STROKE_HISTORICAL),
            ):
                self.remove(c, "historical_stroke", g, "stroke is historical")

    # ---- hypertension (I.C.9.a)
    def _ckd_stage(self) -> str | None:
        """'1'..'5', '3a', '3b', 'ESRD', 'U' (CKD without stage), or None."""
        if self.doc.has(ESRD, current=True):
            return "ESRD"
        for c in self.find("N18."):
            n = self._n(c.code)
            return {
                "N18.1": "1",
                "N18.2": "2",
                "N18.30": "3",
                "N18.31": "3a",
                "N18.32": "3b",
                "N18.4": "4",
                "N18.5": "5",
                "N18.6": "ESRD",
                "N18.9": "U",
            }.get(n, "U")
        hits = self.doc.affirmed(CKD, current=True)
        if not hits:
            return None
        roman = {"I": "1", "II": "2", "III": "3", "IV": "4", "V": "5"}
        for m in CKD_STAGE.finditer(self.doc.text):
            if not any(abs(h.start - m.start()) < 5 for h in hits):
                continue
            raw = re.sub(r"\s+", "", m.group(1)).upper()
            sub = raw[-1].lower() if raw[-1] in "AB" else ""
            base = raw[:-1] if sub else raw
            base = roman.get(base, base)
            if base in {"1", "2", "3", "4", "5"}:
                return base + (sub if base == "3" else "")
        return "U"

    def _heart_failure_code(self) -> str:
        sent = " ".join(
            self.doc.sentence(m.start) for m in self.doc.affirmed(HEART_FAILURE, current=True)
        ).lower()
        if re.search(r"combined|systolic and diastolic", sent):
            typ = "4"
        elif re.search(r"systolic|hfref|reduced ejection", sent):
            typ = "2"
        elif re.search(r"diastolic|hfpef|preserved ejection", sent):
            typ = "3"
        else:
            return "I50.9"
        if "acute on chronic" in sent:
            acu = "3"
        elif re.search(r"\bacute\b", sent):
            acu = "1"
        elif re.search(r"\bchronic\b", sent):
            acu = "2"
        else:
            acu = "0"
        return f"I50.{typ}{acu}"

    def _hypertension_combinations(self) -> None:
        g = "I.C.9.a (hypertension with heart failure / CKD: causal relationship presumed)"
        htn_codes = self.find("I10", "I11.", "I12.", "I13.")
        htn_doc = [
            m for m in self.doc.affirmed(HTN) if not NOT_ESSENTIAL_HTN.search(self.doc.sentence(m.start))
        ]
        if (
            not (htn_codes or htn_doc)
            or self.has_code("I15.", "O10", "O11", "O13", "O16")
            or self.doc.raw(HTN_UNRELATED)
        ):
            return
        hf = self.has_code("I50.") or self.doc.has(HEART_FAILURE, current=True)
        stage = self._ckd_stage()
        if not hf and stage is None:
            return
        if hf and stage:
            target = "I13.2" if stage in ("5", "ESRD") else "I13.0"
        elif hf:
            target = "I11.0"
        else:
            target = "I12.0" if stage in ("5", "ESRD") else "I12.9"
        ev = self.doc.evidence(HTN, HEART_FAILURE if hf else CKD, CKD if (hf and stage) else HEART_FAILURE)
        reason = (
            "hypertension with "
            + " and ".join(
                x for x in (["heart failure"] if hf else []) + (["chronic kidney disease"] if stage else [])
            )
            + " is coded as a combination even without an explicit link"
        )
        first = htn_codes[0] if htn_codes else None
        if first is not None:
            self.replace(first, target, "hypertension_combination", g, reason, ev)
        else:
            self.add(target, "hypertension_combination", g, reason, ev, entity="hypertension")
        for c in self.find("I10", "I11.", "I12.", "I13."):
            if self._n(c.code) != target:
                self.remove(c, "hypertension_combination", g, f"superseded by combination code {target}")
        # use additional code: type of heart failure (I50.-) and stage of CKD (N18.-), dialysis status
        if hf and not self.has_code("I50."):
            self.add(
                self._heart_failure_code(),
                "hypertension_combination",
                g + " - use additional code I50.-",
                "type of heart failure",
                self.doc.evidence(HEART_FAILURE),
                entity="heart failure",
            )
        self._ensure_ckd_stage(stage, g + " - use additional code N18.-")

    def _ensure_ckd_stage(self, stage: str | None, guideline: str) -> None:
        if not stage:
            return
        code = {
            "1": "N18.1",
            "2": "N18.2",
            "3": "N18.30",
            "3a": "N18.31",
            "3b": "N18.32",
            "4": "N18.4",
            "5": "N18.5",
            "ESRD": "N18.6",
            "U": "N18.9",
        }[stage]
        if not self.has_code("N18."):
            self.add(
                code,
                "ckd_stage",
                guideline,
                "stage of chronic kidney disease",
                self.doc.evidence(CKD),
                entity="chronic kidney disease",
            )
        if stage == "ESRD" and self.doc.has(DIALYSIS, current=True) and not self.has_code("Z99.2"):
            self.add(
                "Z99.2",
                "dialysis_status",
                "I.C.14.a.1 / N18.6 use additional code Z99.2",
                "dependence on renal dialysis",
                self.doc.evidence(DIALYSIS),
                entity="dialysis",
            )

    # ---- diabetes
    def _dm_type_prefix(self) -> str | None:
        for c in self.find("E08.", "E09.", "E10.", "E11.", "E13."):
            return self._n(c.code)[:3]
        return None

    def _ensure_diabetes(self) -> None:
        """A documented, current diabetes diagnosis must be coded (type from documentation; default E11)."""
        if self._dm_type_prefix() or self.has_code("O24."):
            return
        dm = self.doc.affirmed(
            _rx(r"diabetes(?! insipidus)", r"\bT[12]DM\b", r"\bDM[12]?\b", r"\bNIDDM\b", r"\bIDDM\b"),
            current=True,
        )
        dm = [
            m
            for m in dm
            if not re.search(
                r"gestational|insipidus|pre-?diabet|family history", self.doc.sentence(m.start), re.I
            )
        ]
        if not dm:
            return
        prefix = "E10" if self.doc.has(TYPE1) and not self.doc.has(TYPE2) else "E11"
        self.add(
            f"{prefix}.9",
            "diabetes_documented",
            "I.C.4.a.2 (type not documented: default E11) / code all documented conditions",
            "documented diabetes was not coded",
            [self.doc.sentence(dm[0].start)],
            entity="diabetes",
        )

    def _diabetes_ckd(self) -> None:
        g = "I.A.15 'with' / Index: Diabetes, with, chronic kidney disease (E08-E13.22 + N18.-)"
        prefix = self._dm_type_prefix()
        if (
            not prefix
            or self.has_code(f"{prefix}.22")
            or self.doc.raw(DM_UNRELATED)
            or self.doc.raw(CKD_OTHER_CAUSE)
        ):
            return
        stage = self._ckd_stage()
        if stage is None:
            return
        target = f"{prefix}.22"
        unspecified = self.find(f"{prefix}.9")
        ev = self.doc.evidence(re.compile(r"diabet", re.I), CKD)
        if unspecified:
            self.replace(
                unspecified[0],
                target,
                "diabetes_ckd",
                g,
                "diabetes with chronic kidney disease (linked by 'with')",
                ev,
            )
        else:
            self.add(
                target,
                "diabetes_ckd",
                g,
                "diabetes with chronic kidney disease (linked by 'with')",
                ev,
                entity="diabetic chronic kidney disease",
                position=self.codes.index(self.find(prefix)[0]) + 1 if self.find(prefix) else None,
            )
        self._ensure_ckd_stage(stage, g)

    def _long_term(self, pattern: re.Pattern[str], exclude: re.Pattern[str] | None = None) -> list[Entity]:
        out = []
        for m in self.doc.affirmed(pattern):
            sent = self.doc.sentence(m.start)
            if exclude is not None and exclude.search(sent):
                continue
            if re.search(
                r"\b(?:start|starting|initiate|consider|plan to|will begin|recommend)\w*\b", sent, re.I
            ) and not re.search(
                r"\b(?:on|takes|taking|continue|current|home|daily|nightly|weekly|bid|tid)\b", sent, re.I
            ):
                continue
            out.append(m)
        return out

    def _diabetes_glycemia(self) -> None:
        """E16.- / R73.- in a diabetic patient -> diabetes with hypoglycemia / hyperglycemia (Excludes1)."""
        prefix = self._dm_type_prefix()
        if not prefix:
            return
        g_hypo = "Index: Diabetes, with, hypoglycemia E08-E13.64- (E16.- Excludes1 diabetes)"
        for c in self.find("E16.0", "E16.1", "E16.2"):
            coma = self.doc.has(COMA, current=True) and not re.search(
                r"without coma|no (?:loss of consciousness|coma)", self.doc.text, re.I
            )
            self.replace(
                c,
                f"{prefix}.641" if coma else f"{prefix}.649",
                "diabetic_hypoglycemia",
                g_hypo,
                "hypoglycemia in a patient with diabetes",
                self.doc.evidence(HYPOGLYCEMIA),
            )
        if self.has_code(f"{prefix}.64"):
            for c in self.find(f"{prefix}.9"):
                self.remove(c, "diabetic_hypoglycemia", g_hypo, "diabetes with hypoglycemia is coded")
        g_hyper = "Index: Diabetes, with, hyperglycemia E08-E13.65 (R73.- Excludes1 diabetes)"
        for c in self.find("R73.0", "R73.9"):
            if self.has_code(f"{prefix}.65"):
                self.remove(
                    c, "diabetic_hyperglycemia", g_hyper, "hyperglycemia is part of the diabetes code"
                )
            else:
                self.replace(
                    c,
                    f"{prefix}.65",
                    "diabetic_hyperglycemia",
                    g_hyper,
                    "hyperglycemia in a patient with diabetes",
                )

    def _diabetic_retinopathy(self) -> None:
        """E08-E13.31-.34: severity, macular edema and laterality from documentation (7th character)."""
        g = "Index: Diabetes, with, retinopathy, nonproliferative mild/moderate/severe, with/without macular edema"
        prefix = self._dm_type_prefix()
        retino = self.doc.affirmed(_rx(r"retinopath"), current=True)
        if not prefix or not retino:
            return
        sent = " ".join(self.doc.sentence(m.start) for m in retino)
        if re.search(r"\bproliferative\b", sent, re.I) and not re.search(r"non-?proliferative", sent, re.I):
            return  # proliferative retinopathy has more axes (traction detachment...): leave to the coder
        sev = (
            "2"
            if re.search(r"\bmild\b", sent, re.I)
            else "3"
            if re.search(r"\bmoderate\b", sent, re.I)
            else "4"
            if re.search(r"\bsevere\b", sent, re.I)
            else "1"
        )
        edema_aff = self.doc.has(_rx(r"macular edema", r"\bDME\b", r"\bCSME\b"), current=True)
        edema = "1" if edema_aff else "9"
        if sev == "1":
            target = f"{prefix}.31{edema}"
        else:
            side = self._side(retino) or ""
            if re.search(r"both eyes|\bOU\b", sent, re.I):
                side = "B"
            lat = {"B": "3", "R": "1", "L": "2"}.get(side, "9")
            target = f"{prefix}.3{sev}{edema}{lat}"
        existing = self.find(f"{prefix}.3")
        if existing and self._n(existing[0].code) == target:
            return
        ev = [self.doc.sentence(retino[0].start)]
        if existing:
            self.replace(
                existing[0],
                target,
                "diabetic_retinopathy",
                g,
                "severity, macular edema and eye from documentation",
                ev,
            )
            for c in self.find(f"{prefix}.3"):
                if self._n(c.code) != target:
                    self.remove(c, "diabetic_retinopathy", g, f"superseded by {target}")
        elif self.has_code(prefix):
            old = self.find(f"{prefix}.9")
            if old:
                self.replace(old[0], target, "diabetic_retinopathy", g, "diabetes with retinopathy", ev)
            else:
                self.add(
                    target,
                    "diabetic_retinopathy",
                    g,
                    "diabetes with retinopathy",
                    ev,
                    entity="diabetic retinopathy",
                )

    def _angina_type(self) -> None:
        g = "Index: Arteriosclerosis, coronary, native vessel, with angina pectoris (type of angina) I25.11-"
        for c in self.find("I25.119"):
            for pat, code in (
                (_rx(r"unstable angina"), "I25.110"),
                (_rx(r"vasospas", r"spasm"), "I25.111"),
                (_rx(r"refractory angina"), "I25.112"),
                (_rx(r"stable angina", r"exertional angina", r"chronic stable angina"), "I25.118"),
            ):
                if self.doc.has(pat, current=True):
                    self.replace(
                        c, code, "angina_type", g, "type of angina documented", self.doc.evidence(pat)
                    )
                    break

    def _influenza_manifestation(self) -> None:
        g = "Index: Influenza (due to identified/unidentified virus) NOS J10.1 / J11.1; with pneumonia J1x.0-"
        if self.doc.has(PNEUMONIA, current=True):
            return
        for c in self.find("J10.0", "J11.0"):
            self.replace(
                c, self._n(c.code)[:4] + "1", "influenza_manifestation", g, "pneumonia is not documented"
            )

    def _antineoplastic_therapy(self) -> None:
        if not (self.has_code("C50.", "Z85.3", "D05.") or self.doc.has(_rx(r"breast cancer"))):
            return
        for code, pat, label in (
            ("Z79.811", AROMATASE, "aromatase inhibitors"),
            ("Z79.810", SERM, "selective estrogen receptor modulators"),
        ):
            if self.has_code(code):
                continue
            hits = self._long_term(pat)
            if hits:
                self.add(
                    code,
                    "antineoplastic_therapy",
                    "Chapter 21 Z79.81- (long-term use of hormonal agents)",
                    f"long-term (current) use of {label} documented",
                    [self.doc.sentence(hits[0].start)],
                    entity=hits[0].text,
                )

    def _coronary_stent_status(self) -> None:
        if self.has_code("Z95.5") or self.has_code("T82."):
            return
        # Status code only for a stent from a previous encounter; a stent placed now is a procedure, not a status.
        hits = [
            m
            for m in self.doc.mentions(CORONARY_STENT)
            if not (m.negated or m.family or m.hypothetical)
            and (m.historical or STENT_PREVIOUS.search(self.doc.sentence(m.start)))
            and not (
                STENT_NOW.search(self.doc.sentence(m.start))
                and not STENT_PREVIOUS.search(self.doc.sentence(m.start))
            )
        ]
        if hits:
            self.add(
                "Z95.5",
                "coronary_stent_status",
                "I.C.21.c.3 status Z codes (Z95.5 coronary angioplasty implant)",
                "presence of coronary stent documented",
                [self.doc.sentence(hits[0].start)],
                entity="coronary stent",
            )

    def _diabetes_drug_therapy(self) -> None:
        g = "I.C.4.a.3 (long-term use of insulin / oral hypoglycemic / injectable non-insulin drugs)"
        prefix = self._dm_type_prefix()
        if not prefix:
            return
        specs = [
            ("Z79.84", ORAL_HYPOGLYCEMIC, None, "oral hypoglycemic drugs"),
            ("Z79.85", INJECTABLE_NON_INSULIN, None, "injectable non-insulin antidiabetic drugs"),
        ]
        if prefix != "E10":  # insulin is inherent to type 1; Z79.4 is optional there
            specs.insert(0, ("Z79.4", INSULIN, INSULIN_TEMPORARY, "insulin"))
        for code, pat, excl, label in specs:
            if self.has_code(code):
                continue
            hits = self._long_term(pat, excl)
            if hits:
                self.add(
                    code,
                    "diabetes_drug_therapy",
                    g,
                    f"long-term (current) use of {label} documented",
                    [self.doc.sentence(hits[0].start)],
                    entity=hits[0].text,
                )

    def _anticoagulant_therapy(self) -> None:
        if self.has_code("Z79.01"):
            return
        hits = self._long_term(ANTICOAGULANT)
        if hits and self.has_code("I48.", "I26.", "I82.", "Z95.2", "Z86.7", "I63.", "D68."):
            self.add(
                "Z79.01",
                "anticoagulant_therapy",
                "Chapter 21 Z79 (long-term drug therapy)",
                "long-term (current) use of anticoagulants documented",
                [self.doc.sentence(hits[0].start)],
                entity=hits[0].text,
            )

    def _diabetic_foot_ulcer(self) -> None:
        g = "I.C.4 / E11.621 use additional code L97.4-/L97.5- (site, side, depth)"
        prefix = self._dm_type_prefix()
        if not prefix:
            return
        ulcers = [
            m
            for m in self.doc.affirmed(re.compile(r"\bulcer", re.I), current=True)
            if FOOT_SITE.search(self.doc.sentence(m.start))
        ]
        if not ulcers:
            return
        for c in self.find(f"{prefix}.622"):
            self.replace(c, f"{prefix}.621", "diabetic_foot_ulcer", g, "ulcer is on the foot")
        if not self.has_code(f"{prefix}.621") and (self.find(f"{prefix}.9") or self.has_code(prefix)):
            old = self.find(f"{prefix}.9")
            ev = [self.doc.sentence(ulcers[0].start)]
            if old:
                self.replace(
                    old[0], f"{prefix}.621", "diabetic_foot_ulcer", g, "diabetes with foot ulcer", ev
                )
            else:
                self.add(
                    f"{prefix}.621",
                    "diabetic_foot_ulcer",
                    g,
                    "diabetes with foot ulcer",
                    ev,
                    entity="diabetic foot ulcer",
                )
        if self.has_code("L97."):
            return
        sent = " ".join(self.doc.sentence(m.start) for m in ulcers)
        site = "4" if re.search(r"heel|midfoot", sent, re.I) else "5"
        side = "1" if RIGHT.search(sent) else "2" if LEFT.search(sent) else "0"
        depth = next((d for d, p in ULCER_DEPTH if p.search(sent)), "9")
        self.add(
            f"L97.{site}{side}{depth}",
            "diabetic_foot_ulcer",
            g,
            "site, laterality and depth of the foot ulcer",
            [self.doc.sentence(ulcers[0].start)],
            entity="foot ulcer",
        )

    # ---- tobacco
    def _tobacco(self) -> None:
        g_former = "Index: History, personal, nicotine dependence Z87.891"
        g_current = "Index: Smoker - see Dependence, drug, nicotine (F17.2-)"
        current = self.doc.has(CURRENT_SMOKER, current=True)
        former = self.doc.has(FORMER_SMOKER) and not current
        for c in self.find("Z72.0", "F17."):
            if former and not self.replace(
                c,
                "Z87.891",
                "tobacco_status",
                g_former,
                "former smoker; not current use",
                self.doc.evidence(FORMER_SMOKER),
            ):
                self.remove(c, "tobacco_status", g_former, "former smoker; not current use")
        for c in self.find("Z72.0"):
            if current and self.doc.has(SMOKER, current=True):
                code = "F17.210" if self.doc.raw(CIGARETTE) else "F17.200"
                self.replace(
                    c,
                    code,
                    "tobacco_status",
                    g_current,
                    "documented current smoker",
                    self.doc.evidence(CURRENT_SMOKER),
                )
        if former and not self.has_code("Z87.891", "F17.", "Z72.0"):
            self.add(
                "Z87.891",
                "tobacco_status",
                g_former,
                "former smoker documented",
                self.doc.evidence(FORMER_SMOKER),
                entity="former smoker",
            )

    # ---- pregnancy
    def _weeks(self) -> int | None:
        m = WEEKS.search(self.doc.text) or WEEKS_LOOSE.search(self.doc.text)
        if not m:
            return None
        w = int(m.group(1))
        return w if 1 <= w <= 45 else None

    def _pregnancy_supervision(self) -> None:
        g = "I.C.15.b (Z34 supervision of normal pregnancy) / I.C.15.a.4 (Z3A weeks of gestation)"
        weeks = self._weeks()
        pregnant = self.has_code("O", "Z34.") or self.doc.has(PRENATAL)
        if not pregnant:
            return
        if weeks is not None and not self.has_code("Z3A."):
            code = "Z3A.01" if weeks < 8 else ("Z3A.49" if weeks > 42 else f"Z3A.{weeks:02d}")
            self.add(
                code,
                "weeks_of_gestation",
                g,
                f"{weeks} weeks of gestation documented",
                self.doc.evidence(WEEKS if WEEKS.search(self.doc.text) else WEEKS_LOOSE),
                entity="weeks of gestation",
            )
        complicated = self.has_code("O") or self.doc.has(HIGH_RISK_PREG)
        if complicated or not self.doc.has(PRENATAL):
            return
        tri = None
        if weeks is not None:
            tri = "1" if weeks < 14 else ("2" if weeks < 28 else "3")
        else:
            t = re.search(r"\b(first|second|third|1st|2nd|3rd)\s+trimester", self.doc.text, re.I)
            if t:
                tri = {"first": "1", "1st": "1", "second": "2", "2nd": "2", "third": "3", "3rd": "3"}[
                    t.group(1).lower()
                ]
        if FIRST_PREGNANCY.search(self.doc.text):
            base = "Z34.0"
        elif LATER_PREGNANCY.search(self.doc.text):
            base = "Z34.8"
        else:
            base = "Z34.9"
        target = f"{base}{tri or '0'}"
        existing = self.find("Z34.")
        ev = self.doc.evidence(PRENATAL)
        if existing:
            if self._n(existing[0].code) != target:
                self.replace(
                    existing[0],
                    target,
                    "pregnancy_supervision",
                    g,
                    "gravidity/trimester from documentation",
                    ev,
                )
        else:
            self.add(
                target,
                "pregnancy_supervision",
                g,
                "routine supervision of normal pregnancy",
                ev,
                entity="prenatal visit",
                position=0,
            )

    # ---- dementia (etiology/manifestation)
    def _alzheimer_dementia(self) -> None:
        g = "Index: Disease, Alzheimer's, with dementia G30.- [F02.8-] (code first etiology)"
        for c in self.find("G30.9", "G30.8"):
            if self.doc.has(LATE_ONSET):
                self.replace(
                    c, "G30.1", "alzheimer_onset", g, "late onset documented", self.doc.evidence(LATE_ONSET)
                )
            elif self.doc.has(EARLY_ONSET):
                self.replace(
                    c, "G30.0", "alzheimer_onset", g, "early onset documented", self.doc.evidence(EARLY_ONSET)
                )
        if not self.has_code("G30.") or not self.doc.has(DEMENTIA):
            return
        sev = next((s for s, p in SEVERITY.items() if self.doc.has(p)), "")
        beh = next((b for b, p in BEHAVIOR if self.doc.has(p, current=True)), "0")
        if sev:
            target = f"F02.{sev}{beh if beh != '11' else '11'}"
        else:
            target = {"0": "F02.80", "11": "F02.811", "2": "F02.82", "3": "F02.83", "4": "F02.84"}[beh]
        for c in self.find("F03.", "F02."):
            if self._n(c.code) != target:
                self.replace(
                    c,
                    target,
                    "dementia_in_other_disease",
                    g,
                    "dementia due to Alzheimer's disease (manifestation F02, severity/behavior from note)",
                    self.doc.evidence(DEMENTIA),
                )
        if not self.has_code("F02."):
            g30 = self.find("G30.")[0]
            self.add(
                target,
                "dementia_in_other_disease",
                g,
                "dementia due to Alzheimer's disease (manifestation)",
                self.doc.evidence(DEMENTIA),
                entity="dementia",
                position=self.codes.index(g30) + 1,
            )

    # ---- influenza (I.C.10.c)
    def _influenza_identified(self) -> None:
        g = "I.C.10.c (influenza due to identified virus: provider statement of influenza A/B)"
        if self.doc.has(FLU_IDENTIFIED) and not self.doc.raw(FLU_NOVEL):
            for c in self.find("J11."):
                self.replace(
                    c,
                    "J10." + self._n(c.code)[4:],
                    "influenza_identified",
                    g,
                    "influenza type documented as identified",
                    self.doc.evidence(FLU_IDENTIFIED),
                )
        if self.has_code("J10.0", "J11.0", "J09.X1"):
            for c in self.find("J18.9"):
                self.remove(
                    c,
                    "influenza_pneumonia",
                    "Index: Influenza, with, pneumonia (combination code)",
                    "unspecified pneumonia is included in the influenza combination code",
                )

    def _asthma_copd_exacerbation(self) -> None:
        g = "Index: Asthma/COPD, with exacerbation (combination code)"
        exac = self.doc.has(EXACERBATION, current=True)
        status = self.doc.has(STATUS_ASTHMATICUS, current=True)
        for c in self.find("J45."):
            n = self._n(c.code)
            if len(n) == 6 and n[-1] == "0" and n[4] in "2345":
                if status:
                    self.replace(
                        c,
                        n[:-1] + "2",
                        "asthma_exacerbation",
                        g,
                        "status asthmaticus documented",
                        self.doc.evidence(STATUS_ASTHMATICUS),
                    )
                elif exac:
                    self.replace(
                        c,
                        n[:-1] + "1",
                        "asthma_exacerbation",
                        g,
                        "acute exacerbation documented",
                        self.doc.evidence(EXACERBATION),
                    )
        for c in self.find("J44.9"):
            if exac:
                self.replace(
                    c,
                    "J44.1",
                    "copd_exacerbation",
                    g,
                    "acute exacerbation documented",
                    self.doc.evidence(EXACERBATION),
                )

    def _osteoarthritis_site(self) -> None:
        g = "Index: Osteoarthritis, knee M17.- / hip M16.- (site and laterality)"
        oa = self.doc.affirmed(OA, current=True)
        if not oa:
            return
        for joint, pat, base in (("knee", KNEE, "M17"), ("hip", HIP, "M16")):
            near = [m for m in oa if pat.search(self.doc.sentence(m.start))]
            if not near:
                continue
            side = self._joint_side(near, joint)
            target = f"{base}.0" if side == "B" else f"{base}.1" + {"R": "1", "L": "2"}.get(side or "", "0")
            olds = self.find("M19.0", "M19.9", f"{base}.")
            if not any(self._n(o.code) == target for o in olds):
                post = re.search(
                    r"post[- ]?traumatic|secondary", " ".join(self.doc.sentence(m.start) for m in near), re.I
                )
                if post:
                    continue
                ev = [self.doc.sentence(near[0].start)]
                if olds:
                    self.replace(
                        olds[0],
                        target,
                        "osteoarthritis_site",
                        g,
                        f"primary osteoarthritis of {joint}, laterality from note",
                        ev,
                    )
                else:
                    self.add(
                        target,
                        "osteoarthritis_site",
                        g,
                        f"primary osteoarthritis of {joint}",
                        ev,
                        entity=f"{joint} osteoarthritis",
                    )
            for o in self.find("M19.0", "M19.9", f"{base}."):
                if self._n(o.code) != target and (
                    self._n(o.code).startswith("M19") or self._n(o.code)[:5] in (f"{base}.0", f"{base}.1")
                ):
                    self.remove(o, "osteoarthritis_site", g, f"superseded by {target}")

    def _lumbago_sciatica(self) -> None:
        g = "Index: Lumbago, with sciatica M54.4- (combination code)"
        hits = [
            m
            for m in self.doc.affirmed(SCIATICA, current=True)
            if LOW_BACK.search(self.doc.sentence(m.start))
        ]
        if not hits:
            return
        side = self._side(hits)
        target = "M54.4" + {"R": "1", "L": "2"}.get(side or "", "0")
        olds = self.find("M54.5", "M54.3", "M54.4")
        ev = [self.doc.sentence(hits[0].start)]
        if not any(self._n(o.code) == target for o in olds):
            if olds:
                self.replace(
                    olds[0], target, "lumbago_sciatica", g, "low back pain with sciatica, side from note", ev
                )
            else:
                self.add(
                    target,
                    "lumbago_sciatica",
                    g,
                    "low back pain with sciatica",
                    ev,
                    entity="lumbago with sciatica",
                )
        for o in self.find("M54.5", "M54.3", "M54.4"):
            if self._n(o.code) != target:
                self.remove(o, "lumbago_sciatica", g, f"included in combination code {target}")

    def _family_history_site(self) -> None:
        g = "Chapter 21 Z80 (family history of malignant neoplasm, by site)"
        for c in self.find("Z80.9"):
            fam = [
                m
                for m in self.doc.mentions(re.compile(r"cancer|carcinoma|malignan|neoplasm|tumou?r", re.I))
                if m.family
            ]
            sents = " ".join(self.doc.sentence(m.start) for m in fam)
            for code, pat in FAMILY_SITES:
                if pat.search(sents):
                    self.replace(
                        c,
                        code,
                        "family_history_site",
                        g,
                        "site of the relative's cancer is documented",
                        [self.doc.sentence(fam[0].start)],
                    )
                    break

    def _visit_reason(self, pattern: re.Pattern[str]) -> list[Entity]:
        """Mentions stating the purpose of THIS encounter (not a future/recommended/overdue service)."""
        out = []
        head_end = len("\n".join(self.doc.text.splitlines()[:3]))
        for m in self.doc.affirmed(pattern):
            sent = self.doc.sentence(m.start)
            if PLANNED.search(sent):
                continue
            if (
                m.section
                in (
                    "chief_complaint",
                    "assessment",
                    "reason_for_visit",
                    "indication",
                    "impression",
                    "discharge_diagnoses",
                    "postoperative_diagnosis",
                    "preoperative_diagnosis",
                )
                or m.start <= head_end
                or re.search(
                    r"\b(?:indication|impression|reason for visit|here for|presents for|for (?:his|her|an?) )",
                    sent,
                    re.I,
                )
            ):
                out.append(m)
        return out

    def _encounter_reasons(self) -> None:
        exam = self._visit_reason(ANNUAL_EXAM)
        if exam and not self.has_code("Z00.") and (self.age is None or self.age >= 18):
            code = "Z00.01" if self.doc.has(ABNORMAL_FINDINGS) else "Z00.00"
            self.add(
                code,
                "general_exam",
                "I.C.21.c.13 (routine and administrative examinations)",
                "encounter for general adult medical examination",
                [self.doc.sentence(exam[0].start)],
                entity="annual examination",
                position=0,
            )
        screen = self._visit_reason(SCREEN_COLON)
        if screen and not self.has_code("Z12.1"):
            self.add(
                "Z12.11",
                "screening_colonoscopy",
                "I.C.21.c.5 (screening)",
                "encounter for screening colonoscopy",
                [self.doc.sentence(screen[0].start)],
                entity="screening colonoscopy",
                position=0,
            )

    def _integral_symptoms(self) -> None:
        g = "I.B.5 (signs/symptoms routinely associated with a definitive diagnosis are not coded)"
        for dx_prefixes, symptom_prefixes in INTEGRAL_SYMPTOMS:
            dx = self.find(*dx_prefixes)
            if not dx:
                continue
            for c in self.find(*symptom_prefixes):
                self.remove(
                    c, "integral_symptom", g, f"integral to {self._n(dx[0].code)} ({dx[0].description})"
                )

    def _cleanup_conflicts(self) -> None:
        for prefix in ("E08", "E09", "E10", "E11", "E13"):
            fam = self.find(prefix + ".")
            if len([c for c in fam if self._n(c.code)[4:5] not in ("8", "9")]) >= 1:
                for c in self.find(prefix + ".8"):
                    self.remove(
                        c,
                        "diabetes_unspecified_complication",
                        "I.C.4.a (code each documented complication)",
                        "specific complication codes are reported",
                    )
            if len(fam) > 1:
                for c in self.find(prefix + ".9"):
                    self.remove(
                        c,
                        "diabetes_without_complications",
                        "I.C.4.a (diabetes 'without complications' "
                        "cannot be reported with a complication code)",
                        "complication codes are reported",
                    )
        if self.has_code("I11.", "I12.", "I13."):
            for c in self.find("I10"):
                self.remove(
                    c, "hypertension_combination", "I.C.9.a", "included in hypertensive combination code"
                )
        # unspecified code next to a specific code of the same condition (validator would only flag it)
        for unspec, specific in (
            ("I21.9", "I21."),
            ("I50.9", "I50."),
            ("J18.9", "J18."),
            ("E78.5", "E78."),
            ("K21.9", "K21."),
            ("R51.9", "G43."),
            ("A41.9", "A41."),
        ):
            if [c for c in self.find(specific) if self._n(c.code) != unspec]:
                for c in self.find(unspec):
                    self.remove(
                        c,
                        "unspecified_with_specific",
                        "I.A.9 / I.B.2 (code to highest specificity)",
                        "a more specific code for the same condition is reported",
                    )
        specific_ckd = [c for c in self.find("N18.") if self._n(c.code) != "N18.9"]
        if specific_ckd:
            for c in self.find("N18.9"):
                self.remove(
                    c, "ckd_stage", "N18 (code to the documented stage)", "a specific CKD stage is coded"
                )
        # deduplicate
        seen: set[str] = set()
        for c in [c for c in self.codes if c.code_system == ICD]:
            n = self._n(c.code)
            if n in seen:
                self.codes.remove(c)
            seen.add(n)


def apply_guideline_rules(
    codes: list[LLMCode],
    not_coded: list[NotCoded],
    text: str,
    sections: list[Section],
    encounter_type: str,
    snapshot: KBSnapshot | None,
    patient_age: int | None = None,
) -> list[RuleChange]:
    return GuidelineRules(snapshot).apply(codes, not_coded, text, sections, encounter_type, patient_age)
