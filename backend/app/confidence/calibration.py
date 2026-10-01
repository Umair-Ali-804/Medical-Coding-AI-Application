"""Fit confidence weights and pick the review threshold on labeled validation data.

The threshold is chosen from data, not guessed: the smallest score at which the
precision of auto-"standard" suggestions reaches the target (default 95%).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.confidence.scorer import DEFAULT_BIAS, DEFAULT_WEIGHTS, FEATURES, NEUTRAL_AGREEMENT, Calibration


@dataclass
class LabeledSuggestion:
    signals: dict[str, float | None]
    passed_validation: bool
    correct: bool


def _matrix(rows: list[LabeledSuggestion]) -> tuple[np.ndarray, np.ndarray]:
    X = np.array(
        [
            [
                (
                    r.signals.get(f)
                    if r.signals.get(f) is not None
                    else (NEUTRAL_AGREEMENT if f == "agreement" else 0.0)
                )
                for f in FEATURES
            ]
            for r in rows
        ],
        dtype=np.float64,
    )
    y = np.array([1.0 if r.correct else 0.0 for r in rows])
    return X, y


def fit_weights(
    rows: list[LabeledSuggestion], l2: float = 0.5, lr: float = 0.1, epochs: int = 3000
) -> tuple[dict, float]:
    """L2-regularized logistic regression, shrunk toward the default priors."""
    X, y = _matrix(rows)
    prior = np.array([DEFAULT_WEIGHTS[f] for f in FEATURES])
    w, b = prior.copy(), DEFAULT_BIAS
    n = len(y)
    for _ in range(epochs):
        z = X @ w + b
        p = 1 / (1 + np.exp(-z))
        grad_w = X.T @ (p - y) / n + l2 * (w - prior) / n
        grad_b = float(np.mean(p - y))
        w -= lr * grad_w
        b -= lr * grad_b
    return {f: round(float(v), 4) for f, v in zip(FEATURES, w, strict=True)}, round(float(b), 4)


def choose_threshold(
    scores: list[float],
    correct: list[bool],
    passed: list[bool],
    target_precision: float = 0.95,
    min_support: int = 10,
) -> tuple[float, dict]:
    pairs = sorted(
        ((s, c) for s, c, p in zip(scores, correct, passed, strict=True) if p), key=lambda t: -t[0]
    )
    best = None
    tp = fp = 0
    for s, c in pairs:
        tp += int(c)
        fp += int(not c)
        prec = tp / (tp + fp)
        if tp + fp >= min_support and prec >= target_precision:
            best = (s, prec, tp + fp)
    total_correct = sum(1 for c, p in zip(correct, passed, strict=True) if c and p)
    if best is None:
        return 0.99, {
            "achieved_precision": None,
            "auto_standard_share": 0.0,
            "note": "target precision not reachable",
        }
    thr, prec, support = best
    return round(thr, 4), {
        "achieved_precision": round(prec, 4),
        "support": support,
        "auto_standard_share": round(support / max(1, len(scores)), 4),
        "recall_of_correct_at_threshold": round(
            sum(1 for s, c in pairs if s >= thr and c) / max(1, total_correct), 4
        ),
    }


def calibrate(rows: list[LabeledSuggestion], target_precision: float = 0.95) -> Calibration:
    from app.confidence.scorer import score

    if len(rows) >= 30 and len({r.correct for r in rows}) == 2:
        weights, bias = fit_weights(rows)
        source = f"fitted:n={len(rows)}"
    else:
        weights, bias, source = dict(DEFAULT_WEIGHTS), DEFAULT_BIAS, f"priors:n={len(rows)} (too few to fit)"
    cal = Calibration(weights=weights, bias=bias, source=source)
    scores = [score(r.signals, cal) for r in rows]
    thr, info = choose_threshold(
        scores, [r.correct for r in rows], [r.passed_validation for r in rows], target_precision
    )
    cal.threshold = thr
    cal.metrics = {"target_precision": target_precision, **info, "n": len(rows)}
    return cal
