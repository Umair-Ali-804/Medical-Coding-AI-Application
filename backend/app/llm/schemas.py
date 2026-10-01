"""Structured-output contracts for the LLM (validated with Pydantic)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

Category = Literal["condition", "symptom", "procedure", "medication", "finding", "anatomy", "other"]


class LLMEntity(BaseModel):
    text: str = Field(min_length=1, max_length=300, description="Exact span copied verbatim from the note")
    category: Category
    negated: bool = False
    uncertain: bool = False
    historical: bool = False
    family: bool = False
    laterality: str | None = None
    severity: str | None = None
    temporal: str | None = None


class ExtractionOutput(BaseModel):
    entities: list[LLMEntity] = Field(default_factory=list)


class LLMCode(BaseModel):
    code: str = Field(min_length=3, max_length=10)
    code_system: Literal["ICD-10-CM", "CPT", "HCPCS", "ICD-10-PCS"] = "ICD-10-CM"
    description: str = Field(default="", max_length=400)
    entity: str = Field(default="", max_length=300, description="Clinical concept this code represents")
    evidence: list[str] = Field(
        default_factory=list, max_length=5, description="Verbatim quotes from the note"
    )
    rationale: str = Field(default="", max_length=1000)
    confidence: float = Field(ge=0.0, le=1.0)
    from_candidates: bool = True

    @field_validator("code")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip().upper()


class NotCoded(BaseModel):
    entity: str
    reason: str


class CodingOutput(BaseModel):
    codes: list[LLMCode] = Field(default_factory=list)
    not_coded: list[NotCoded] = Field(default_factory=list)


def strict_json_schema(model: type[BaseModel], name: str) -> dict:
    """OpenAI/OpenRouter strict json_schema: every property required, no extras, nullables explicit."""

    def fix(node: dict) -> dict:
        if node.get("type") == "object" or "properties" in node:
            props = node.get("properties", {})
            node["required"] = list(props.keys())
            node["additionalProperties"] = False
            for p in props.values():
                fix(p)
        if "items" in node and isinstance(node["items"], dict):
            fix(node["items"])
        for key in ("anyOf", "allOf", "oneOf"):
            for sub in node.get(key, []):
                fix(sub)
        for k in ("default", "title", "maxItems", "minLength", "maxLength", "minimum", "maximum"):
            node.pop(k, None)
        return node

    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})
    for d in defs.values():
        fix(d)
    fix(schema)
    if defs:
        schema["$defs"] = defs
    return {"name": name, "strict": True, "schema": schema}
