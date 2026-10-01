"""Evaluate the claim scrubber on labeled claims.

    python -m app.evaluation.claims_eval [--file data/evaluation/claims_scrub_cases.jsonl] [--out report.json]

Each case lists the rule ids that MUST fire (deny/review severity). Metrics:
  detection recall     expected rules that fired / expected rules
  false-alarm rate     deny/review rules that fired on a case but were not expected / cases
  clean-claim accuracy cases with no expected rules that came back with no deny/review issues
Add your own denied claims (with the rule you believe caused the denial) to grow this set.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from app.claims.scrubber import DENY, REVIEW, Claim, ClaimScrubber
from app.db.session import session_scope

DEFAULT = Path(__file__).resolve().parents[3] / "data" / "evaluation" / "claims_scrub_cases.jsonl"


def evaluate(path: Path) -> dict:
    cases = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    hit = miss = extra = clean_ok = clean_total = 0
    per_rule: Counter = Counter()
    per_rule_hit: Counter = Counter()
    rows = []
    with session_scope() as db:
        scrubber = ClaimScrubber(db)
        for c in cases:
            res = scrubber.scrub(Claim.from_dict(c["claim"]))
            fired = {i.rule for i in res.issues if i.severity in (DENY, REVIEW)}
            expected = set(c["expected_rules"])
            for r in expected:
                per_rule[r] += 1
                if r in fired:
                    per_rule_hit[r] += 1
            hit += len(expected & fired)
            miss += len(expected - fired)
            unexpected = fired - expected
            extra += len(unexpected)
            if not expected:
                clean_total += 1
                clean_ok += not fired
            rows.append(
                {
                    "claim_id": c["claim_id"],
                    "expected": sorted(expected),
                    "fired": sorted(fired),
                    "missed": sorted(expected - fired),
                    "unexpected": sorted(unexpected),
                    "status": res.status,
                    "risk": res.risk_score,
                }
            )
    total = hit + miss
    return {
        "cases": len(cases),
        "detection_recall": round(hit / total, 4) if total else None,
        "unexpected_findings_per_claim": round(extra / len(cases), 3) if cases else None,
        "clean_claim_accuracy": round(clean_ok / clean_total, 4) if clean_total else None,
        "per_rule_recall": {r: f"{per_rule_hit[r]}/{n}" for r, n in sorted(per_rule.items())},
        "cases_detail": rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(DEFAULT))
    ap.add_argument("--out")
    a = ap.parse_args()
    rep = evaluate(Path(a.file))
    print(
        f"cases={rep['cases']} detection_recall={rep['detection_recall']} "
        f"clean_claim_accuracy={rep['clean_claim_accuracy']} unexpected_per_claim={rep['unexpected_findings_per_claim']}"
    )
    for r in rep["cases_detail"]:
        flag = "OK " if not r["missed"] and not r["unexpected"] else "!! "
        print(f"{flag}{r['claim_id']:36} expected={r['expected']} fired={r['fired']}")
    if a.out:
        Path(a.out).write_text(json.dumps(rep, indent=2))


if __name__ == "__main__":
    main()
