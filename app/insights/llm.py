"""Optional LLM-backed insight provider (F017).

Disabled unless ``AppSettings.llm_ready()``. Sends only ``request.facts`` (the
sanitized structural metadata) - never raw ADF JSON. The ``anthropic`` SDK is
imported lazily so the application runs without the ``llm`` extra.
"""

from __future__ import annotations

import json
from typing import Any

from app.core.config import AppSettings
from app.core.logging import get_logger
from app.insights.interface import InsightProvider, InsightRequest, InsightResult

logger = get_logger(__name__)

_SYSTEM_PROMPT = (
    "You are a data-pipeline documentation assistant. You will receive a JSON object of "
    "DETERMINISTIC structural facts about ONE Azure Data Factory artifact, already extracted "
    "by a separate engine.\n"
    "Rules:\n"
    "- Treat the facts as authoritative and complete. Do NOT invent pipelines, activities, "
    "columns, lineage edges or relationships that are not present in the facts.\n"
    "- Do NOT ask for or reference the original JSON.\n"
    "- Reply with ONLY a JSON object with keys "
    '"summary" (string) and "observations", "risks", "recommendations" (string arrays). '
    "No markdown, no text outside the JSON.\n"
    "- Keep each list to at most 5 short, specific, actionable items."
)

_MAX_TOKENS = 1024


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else ""
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(v) for v in value)
    if isinstance(value, str) and value:
        return (value,)
    return ()


def parse_result(text: str, *, provider: str) -> InsightResult:
    """Parse a model reply into an :class:`InsightResult` (lenient)."""
    try:
        data = json.loads(_strip_fences(text))
    except (json.JSONDecodeError, TypeError):
        return InsightResult(
            summary=(text or "").strip()[:2000] or "(empty response)",
            provider=provider,
        )
    if not isinstance(data, dict):
        return InsightResult(summary=str(data)[:2000], provider=provider)
    return InsightResult(
        summary=str(data.get("summary", "")).strip() or "(no summary)",
        observations=_as_str_tuple(data.get("observations")),
        risks=_as_str_tuple(data.get("risks")),
        recommendations=_as_str_tuple(data.get("recommendations")),
        provider=provider,
    )


class LLMInsightProvider(InsightProvider):
    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self.name = f"llm:{settings.llm_provider or 'unconfigured'}"

    def _client(self) -> Any:
        try:
            import anthropic
        except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "LLM insights need the optional 'llm' extra: "
                "pip install 'data-pipeline-intelligence[llm]'"
            ) from exc
        return anthropic.Anthropic(api_key=self._settings.llm_api_key)

    def build_payload(self, request: InsightRequest) -> str:
        return json.dumps(
            {"subject": request.subject, "facts": request.facts},
            default=str,
            sort_keys=True,
        )

    def generate(self, request: InsightRequest) -> InsightResult:
        if not self._settings.llm_ready():
            raise RuntimeError(
                "LLM insights are not configured (enable flag / provider / model / key)."
            )
        client = self._client()
        message = client.messages.create(
            model=self._settings.llm_model,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": self.build_payload(request)}],
        )
        text = "".join(
            getattr(block, "text", "")
            for block in getattr(message, "content", [])
            if getattr(block, "type", "") == "text"
        )
        logger.info("llm insight generated subject=%s provider=%s", request.subject, self.name)
        return parse_result(text, provider=self.name)
