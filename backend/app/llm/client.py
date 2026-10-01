"""OpenRouter (OpenAI-compatible) chat client with structured output.

Production behaviours:
  * json_schema structured output (falls back to json_object for models without it)
  * Pydantic validation + one repair round-trip on invalid output
  * retries with exponential backoff + jitter on 408/429/5xx/network errors
  * token + cost accounting (OpenRouter `usage.cost`, else configured prices)
  * provider routing that denies prompt logging/training (`data_collection: deny`)
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError
from tenacity import (
    RetryError,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.core.config import Settings, get_settings
from app.core.errors import UpstreamError
from app.core.metrics import LLM_COST, LLM_ERRORS, LLM_TOKENS
from app.llm.schemas import strict_json_schema

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)
_RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


@dataclass
class LLMResult:
    parsed: BaseModel
    raw_text: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    repaired: bool = False


class _Retryable(Exception):
    pass


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, _Retryable | httpx.TimeoutException | httpx.TransportError)


def extract_json(text: str) -> str:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]
    return text


class OpenRouterClient:
    def __init__(self, settings: Settings | None = None, transport: httpx.BaseTransport | None = None):
        self.s = settings or get_settings()
        if not self.s.openrouter_api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not configured")
        self._client = httpx.Client(
            base_url=self.s.openrouter_base_url.rstrip("/"),
            timeout=httpx.Timeout(self.s.llm_timeout_s, connect=10.0),
            transport=transport,
            headers={
                "Authorization": f"Bearer {self.s.openrouter_api_key.get_secret_value()}",
                "HTTP-Referer": self.s.http_referer,
                "X-Title": self.s.app_name,
            },
        )

    def complete_structured(
        self,
        messages: list[dict],
        schema_model: type[T],
        *,
        schema_name: str,
        model: str | None = None,
    ) -> LLMResult:
        model = model or self.s.llm_model
        started = time.perf_counter()
        body = self._body(messages, schema_model, schema_name, model)
        data = self._post(body, model)
        text = self._content(data)
        in_tok, out_tok, cost = self._usage(data, model)
        repaired = False
        try:
            parsed = schema_model.model_validate_json(extract_json(text))
        except (ValidationError, ValueError) as err:
            # One repair round: show the model its own output and the validation error.
            LLM_ERRORS.labels(model, "invalid_output").inc()
            repair_msgs = messages + [
                {"role": "assistant", "content": text[:20000]},
                {
                    "role": "user",
                    "content": "Your previous reply did not match the required JSON schema. "
                    f"Error: {str(err)[:1500]}\nReturn ONLY the corrected JSON object.",
                },
            ]
            data2 = self._post(self._body(repair_msgs, schema_model, schema_name, model), model)
            text = self._content(data2)
            i2, o2, c2 = self._usage(data2, model)
            in_tok, out_tok, cost = in_tok + i2, out_tok + o2, cost + c2
            try:
                parsed = schema_model.model_validate_json(extract_json(text))
            except (ValidationError, ValueError) as err2:
                LLM_ERRORS.labels(model, "invalid_after_repair").inc()
                raise UpstreamError(
                    "LLM returned invalid structured output", details={"error": str(err2)[:500]}
                )
            repaired = True

        latency = int((time.perf_counter() - started) * 1000)
        LLM_TOKENS.labels(model, "input").inc(in_tok)
        LLM_TOKENS.labels(model, "output").inc(out_tok)
        LLM_COST.labels(model).inc(cost)
        return LLMResult(parsed, text, data.get("model", model), in_tok, out_tok, cost, latency, repaired)

    # -- internals ---------------------------------------------------------------
    def _body(self, messages: list[dict], schema_model: type[BaseModel], name: str, model: str) -> dict:
        body: dict = {
            "model": model,
            "messages": messages,
            "temperature": self.s.llm_temperature,
            "max_tokens": self.s.llm_max_tokens,
            "usage": {"include": True},
            # PHI hygiene: only route to providers that do not store/train on prompts
            "provider": {"data_collection": "deny", "require_parameters": self.s.llm_use_json_schema},
        }
        if self.s.llm_use_json_schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": strict_json_schema(schema_model, name),
            }
        else:
            body["response_format"] = {"type": "json_object"}
        return body

    def _post(self, body: dict, model: str) -> dict:
        @retry(
            retry=retry_if_exception(_is_retryable),
            stop=stop_after_attempt(self.s.llm_max_retries),
            wait=wait_exponential_jitter(initial=1, max=30),
            reraise=True,
        )
        def _do() -> dict:
            resp = self._client.post("/chat/completions", json=body)
            if resp.status_code in _RETRY_STATUS:
                LLM_ERRORS.labels(model, f"http_{resp.status_code}").inc()
                raise _Retryable(f"HTTP {resp.status_code}")
            if resp.status_code >= 400:
                LLM_ERRORS.labels(model, f"http_{resp.status_code}").inc()
                raise UpstreamError(
                    f"LLM provider error HTTP {resp.status_code}", details={"body": resp.text[:500]}
                )
            data = resp.json()
            if "error" in data and not data.get("choices"):
                raise _Retryable(str(data["error"])[:300])
            return data

        try:
            return _do()
        except (_Retryable, httpx.HTTPError, RetryError) as exc:
            raise UpstreamError(f"LLM provider unavailable: {exc}")

    @staticmethod
    def _content(data: dict) -> str:
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError):
            raise UpstreamError("LLM response missing choices")
        content = msg.get("content")
        if isinstance(content, list):  # some providers return content parts
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        if not content and msg.get("parsed"):
            content = json.dumps(msg["parsed"])
        if not content:
            raise UpstreamError("LLM returned empty content")
        return content

    def _usage(self, data: dict, model: str) -> tuple[int, int, float]:
        u = data.get("usage") or {}
        i, o = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
        cost = u.get("cost")
        if cost is None:
            cost = i / 1e6 * self.s.llm_price_input_per_m + o / 1e6 * self.s.llm_price_output_per_m
        return i, o, float(cost)
