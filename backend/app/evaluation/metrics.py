"""Evaluation metrics and error analysis."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field


@dataclass
class DocResult:
    document_id: str
    gold: set[str]
    predicted: list[str]  # after the baseline's filtering
    auto_accept: list[str] = field(default_factory=list)  # routed to standard review
    unsupported: list[str] = field(default_factory=list)  # evidence not found / nonexistent code
    invalid: list[str] = field(default_factory=list)  # code not in KB / not billable
    retrieved: set[str] = field(default_factory=set)
    negated_predictions: list[str] = field(default_factory=list)
    mandatory: int = 0
    total_suggestions: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    errors: list[dict] = field(default_factory=list)


def prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return round(p, 4), round(r, 4), round(f, 4)


def categorize_errors(doc: DocResult) -> list[dict]:
    """Error taxonomy: wrong specificity, missing (retrieval vs reasoning), extra, negation."""
    pred = set(doc.predicted)
    errors: list[dict] = []
    fn = doc.gold - pred
    fp = pred - doc.gold
    matched_fp: set[str] = set()
    for g in sorted(fn):
        same_cat = [p for p in fp if p[:3] == g[:3] and p not in matched_fp]
        if same_cat:
            matched_fp.add(same_cat[0])
            errors.append({"type": "wrong_specificity", "gold": g, "predicted": same_cat[0]})
        elif g not in doc.retrieved:
            errors.append({"type": "retrieval_failure", "gold": g})
        else:
            errors.append(
                {"type": "missing_code", "gold": g, "note": "retrieved but not selected (reasoning)"}
            )
    for p in sorted(fp - matched_fp):
        if p in doc.negated_predictions:
            errors.append({"type": "negation_error", "predicted": p})
        elif p in doc.invalid:
            errors.append({"type": "validation_failure", "predicted": p})
        else:
            errors.append({"type": "extra_code", "predicted": p})
    return errors


def aggregate(docs: list[DocResult]) -> dict:
    tp = sum(len(d.gold & set(d.predicted)) for d in docs)
    fp = sum(len(set(d.predicted) - d.gold) for d in docs)
    fn = sum(len(d.gold - set(d.predicted)) for d in docs)
    p, r, f = prf(tp, fp, fn)

    # lenient: 3-character category level
    ctp = cfp = cfn = 0
    for d in docs:
        g = {c[:3] for c in d.gold}
        pr = {c[:3] for c in d.predicted}
        ctp, cfp, cfn = ctp + len(g & pr), cfp + len(pr - g), cfn + len(g - pr)
    cp, cr, cf = prf(ctp, cfp, cfn)

    auto = [(c, d) for d in docs for c in d.auto_accept]
    auto_correct = sum(1 for c, d in auto if c in d.gold)
    total_pred = sum(len(d.predicted) for d in docs)
    gold_total = sum(len(d.gold) for d in docs)
    lat = [d.latency_ms for d in docs]
    errors: dict[str, int] = {}
    for d in docs:
        for e in d.errors:
            errors[e["type"]] = errors.get(e["type"], 0) + 1
    total_sugg = sum(d.total_suggestions for d in docs)
    return {
        "documents": len(docs),
        "gold_codes": gold_total,
        "predicted_codes": total_pred,
        "precision": p,
        "recall": r,
        "f1": f,
        "category_precision": cp,
        "category_recall": cr,
        "category_f1": cf,
        "exact_match_accuracy": round(sum(1 for d in docs if set(d.predicted) == d.gold) / len(docs), 4)
        if docs
        else 0,
        "unsupported_code_rate": round(sum(len(d.unsupported) for d in docs) / total_pred, 4)
        if total_pred
        else 0.0,
        "invalid_code_rate": round(sum(len(d.invalid) for d in docs) / max(1, total_sugg), 4),
        "retrieval_recall": round(sum(len(d.gold & d.retrieved) for d in docs) / gold_total, 4)
        if gold_total
        else None,
        "auto_accept_precision": round(auto_correct / len(auto), 4) if auto else None,
        "auto_accept_share": round(len(auto) / total_sugg, 4) if total_sugg else 0.0,
        "mandatory_review_share": round(sum(d.mandatory for d in docs) / total_sugg, 4)
        if total_sugg
        else 0.0,
        "latency_ms_p50": statistics.median(lat) if lat else 0,
        "latency_ms_p95": sorted(lat)[max(0, int(len(lat) * 0.95) - 1)] if lat else 0,
        "cost_per_document_usd": round(sum(d.cost_usd for d in docs) / len(docs), 6) if docs else 0.0,
        "error_breakdown": errors,
    }
