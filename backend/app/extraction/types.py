from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Entity:
    text: str
    category: str
    start: int
    end: int
    normalized: str | None = None
    section: str | None = None
    negated: bool = False
    uncertain: bool = False
    historical: bool = False
    family: bool = False
    hypothetical: bool = False
    laterality: str | None = None
    severity: str | None = None
    temporal: str | None = None
    source: str = "rules"
    assertion_conflict: bool = False
    confidence: float | None = None
    triggers: list[str] = field(default_factory=list)

    @property
    def affirmed(self) -> bool:
        """Present, certain, about the patient, current."""
        return not (self.negated or self.uncertain or self.family or self.hypothetical)

    def overlaps(self, other: Entity) -> bool:
        return self.start < other.end and other.start < self.end

    @property
    def concept_key(self) -> str:
        return (self.normalized or self.text).lower().strip()
