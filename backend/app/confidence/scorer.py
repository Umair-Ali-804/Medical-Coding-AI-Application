"""Platform confidence score (not the LLM's self-reported confidence).

score = sigmoid(bias + sum_i w_i * signal_i) over independent signals:
  evidence   - is the quoted evidence verbatim in the document?
  retrieval  - did hybrid RAG retrieve this code, and how highly?
  validation - deterministic validation outcome
  entity     - does an affirmed extracted entity match the code description?
  agreement  - does an independent second model propose the same code? (optional)
  llm        - model self-reported probability (low weight: LLMs are poorly calibrated)

Default weights are priors; `app.confidence.calibration` fits them (and the review
threshold) on a labeled validation split. Routing:
  rejected by validation              -> system_rejected
  flagged, or score < threshold       -> mandatory review
  otherwise                           -> standard review
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.core.config import get_settings
from app.models.enums import ReviewRoute, ValidationStatus

log = logging.getLogger(__name__)

FEATURES = ("evidence", "retrieval", "validation", "entity", "agreement", "llm")
DEFAULT_WEIGHTS = {
    "evidence": 2.0,
    "retrieval": 2.0,
    "validation": 3.0,
    "entity": 1.5,
    "agreement": 1.5,
    "llm": 1.0,
}
DEFAULT_BIAS = -5.0
NEUTRAL_AGREEMENT = 0.5


@dataclass
class Calibration:
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    bias: float = DEFAULT_BIAS
    threshold: float = 0.80
    source: str = "default-priors"
    metrics: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "weights": self.weights,
            "bias": self.bias,
            "threshold": self.threshold,
            "source": self.source,
            "metrics": self.metrics,
        }

    @classmethod
    def from_json(cls, d: dict) -> Calibration:
        return cls(
            weights={**DEFAULT_WEIGHTS, **d.get("weights", {})},
            bias=float(d.get("bias", DEFAULT_BIAS)),
            threshold=float(d.get("threshold", 0.8)),
            source=d.get("source", "file"),
            metrics=d.get("metrics", {}),
        )


@lru_cache
def load_calibration() -> Calibration:
    s = get_settings()
    if s.calibration_file and Path(s.calibration_file).exists():
        try:
            cal = Calibration.from_json(json.loads(Path(s.calibration_file).read_text()))
            log.info(
                "calibration_loaded", extra={"source": str(s.calibration_file), "threshold": cal.threshold}
            )
            return cal
        except Exception as exc:  # noqa: BLE001
            log.error("calibration_invalid", extra={"error": str(exc)[:200]})
    return Calibration(threshold=s.confidence_threshold)


@dataclass
class ConfidenceResult:
    score: float
    signals: dict[str, float]
    route: str

    def breakdown(self, cal: Calibration) -> dict:
        return {
            "signals": self.signals,
            "weights": cal.weights,
            "bias": cal.bias,
            "threshold": cal.threshold,
            "calibration": cal.source,
        }


def validation_signal(status: str, n_flags: int) -> float:
    if status == ValidationStatus.REJECTED:
        return 0.0
    if status == ValidationStatus.FLAGGED:
        return max(0.1, 0.4 - 0.1 * (n_flags - 1))
    return 1.0


def retrieval_signal(score: float | None, rank: int | None) -> float:
    if score is None or rank is None:
        return 0.0
    rank_factor = 1.0 if rank == 1 else 0.85 if rank <= 3 else 0.7
    return round(max(0.0, min(1.0, score)) * rank_factor, 4)


def score(signals: dict[str, float | None], cal: Calibration | None = None) -> float:
    cal = cal or load_calibration()
    z = cal.bias
    for f in FEATURES:
        v = signals.get(f)
        if v is None:
            v = NEUTRAL_AGREEMENT if f == "agreement" else 0.0
        z += cal.weights.get(f, 0.0) * float(v)
    return 1.0 / (1.0 + math.exp(-z))


def assess(
    signals: dict[str, float | None], validation_status: str, cal: Calibration | None = None
) -> ConfidenceResult:
    cal = cal or load_calibration()
    s = round(score(signals, cal), 4)
    if validation_status == ValidationStatus.REJECTED:
        route = ReviewRoute.SYSTEM_REJECTED
    elif validation_status == ValidationStatus.FLAGGED or s < cal.threshold:
        route = ReviewRoute.MANDATORY
    else:
        route = ReviewRoute.STANDARD
    clean = {k: (round(float(v), 4) if v is not None else None) for k, v in signals.items()}
    return ConfidenceResult(s, clean, route)
