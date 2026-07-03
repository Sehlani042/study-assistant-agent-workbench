from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Protocol


@dataclass
class PageContext:
    document_summary: str
    group_summary: str
    page_no: int
    page_text: str
    page_formulas: list[dict]
    local_context: list[dict]


class LLMClient(Protocol):
    provider_name: str

    def summarize_document(self, pages: list[dict], *, instruction: str | None = None) -> dict:
        ...

    def summarize_group(
        self,
        document_summary: str,
        group: dict,
        pages: list[dict],
        *,
        instruction: str | None = None,
    ) -> dict:
        ...

    def explain_page(
        self,
        context: PageContext,
        *,
        language: str,
        model_tier: str,
        feedback: str | None = None,
        instruction: str | None = None,
    ) -> dict:
        ...

    def answer_page_question(
        self,
        *,
        question: str,
        language: str,
        page: dict,
        explanation: dict,
        local_context: list[dict],
        global_summary: str,
        instruction: str | None = None,
    ) -> dict:
        ...

    def recognize_formulas_from_visual(self, *, page_text: str, instruction: str | None = None) -> list[dict]:
        ...

    def describe_page_image(
        self,
        *,
        image_path: Path,
        page_text: str,
        instruction: str | None = None,
    ) -> dict[str, Any]:
        ...

    def translate_page_text(
        self,
        *,
        page_text: str,
        language: str,
        instruction: str | None = None,
    ) -> str:
        ...
