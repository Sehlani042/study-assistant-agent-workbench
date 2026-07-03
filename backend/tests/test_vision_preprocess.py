from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import fitz  # type: ignore

from app.config import get_settings
from app.api.routes_documents import _build_agent_graph
from app.llm.openai_client import OpenAIClient
from app.pipeline.preprocess import extract_pages


_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


class _FormulaOnlyClient:
    provider_name = "mock"

    def recognize_formulas_from_visual(self, *, page_text: str, instruction: str | None = None) -> list[dict]:
        return []


class _VisionClient(_FormulaOnlyClient):
    provider_name = "openai"

    def describe_page_image(
        self,
        *,
        image_path: Path,
        page_text: str,
        instruction: str | None = None,
    ) -> dict[str, Any]:
        return {
            "visual_summary": "这页展示学习率过大时会越过最优点。",
            "text_blocks": [
                {
                    "text": "High learning rate overshoots the optimum",
                    "kind": "title",
                    "confidence": 0.93,
                },
                {
                    "text": "Use smaller steps near the minimum.",
                    "kind": "paragraph",
                    "confidence": 0.88,
                },
            ],
            "chart_notes": ["Loss curve descends, then jumps across the basin."],
        }


class _FailingVisionClient(_FormulaOnlyClient):
    provider_name = "openai"

    def describe_page_image(
        self,
        *,
        image_path: Path,
        page_text: str,
        instruction: str | None = None,
    ) -> dict[str, Any]:
        raise AssertionError("PDF pages should not call the OpenAI vision fallback")


def _blank_pdf(path: Path) -> None:
    doc = fitz.open()
    doc.new_page(width=960, height=540)
    doc.save(path)
    doc.close()


def test_extract_pages_uses_vision_blocks_for_visual_ppt_pages(tmp_path: Path) -> None:
    pdf_path = tmp_path / "visual-slide.pdf"
    _blank_pdf(pdf_path)

    pages = extract_pages(
        pdf_path,
        tmp_path / "doc",
        _FormulaOnlyClient(),
        source_type="pptx",
        vision_client=_VisionClient(),
    )

    assert len(pages) == 1
    page = pages[0]
    assert "High learning rate overshoots the optimum" in page["text_content"]
    assert "Loss curve descends" in page["text_content"]
    assert page["layout_blocks"][0]["source"] == "openai_vision"
    assert page["layout_blocks"][0]["kind"] == "title"
    assert page["layout_blocks"][0]["confidence"] == 0.93


def test_extract_pages_does_not_call_vision_for_plain_pdf(tmp_path: Path) -> None:
    pdf_path = tmp_path / "plain.pdf"
    _blank_pdf(pdf_path)

    pages = extract_pages(
        pdf_path,
        tmp_path / "doc",
        _FormulaOnlyClient(),
        source_type="pdf",
        vision_client=_FailingVisionClient(),
    )

    assert len(pages) == 1
    assert not any(block.get("source") == "openai_vision" for block in pages[0]["layout_blocks"])


def test_settings_loads_openai_key_from_secret_path(tmp_path: Path, monkeypatch) -> None:
    key_path = tmp_path / "openai-key.txt"
    key_path.write_text("unit-test-openai-from-secret-path\n", encoding="utf-8")

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'assistant.db'}")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY_PATH", str(key_path))
    monkeypatch.setenv("OPENAI_VISION_MODEL", "gpt-5.4-mini")

    settings = get_settings()

    assert settings.openai_api_key == "unit-test-openai-from-secret-path"
    assert settings.openai_vision_model == "gpt-5.4-mini"


def test_openai_vision_request_sends_image_payload(monkeypatch, tmp_path: Path) -> None:
    image_path = tmp_path / "slide.png"
    image_path.write_bytes(_ONE_PIXEL_PNG)
    seen_payload: dict[str, Any] = {}

    class _Response:
        status_code = 200
        text = "{}"

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {
                                        "visual_summary": "A tiny test slide.",
                                        "text_blocks": [
                                            {
                                                "text": "Learning rate",
                                                "kind": "title",
                                                "confidence": 0.91,
                                            }
                                        ],
                                        "chart_notes": [],
                                    }
                                ),
                            }
                        ],
                    }
                ]
            }

    class _FakeHttpxClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, headers=None, json=None):
            seen_payload.update(json or {})
            return _Response()

    monkeypatch.setattr("app.llm.openai_client.httpx.Client", _FakeHttpxClient)

    client = OpenAIClient(api_key="unit-test-openai", model="gpt-5.4-mini")
    parsed = client.describe_page_image(image_path=image_path, page_text="")

    assert parsed["visual_summary"] == "A tiny test slide."
    assert seen_payload["model"] == "gpt-5.4-mini"
    content = seen_payload["input"][0]["content"]
    assert content[0]["type"] == "input_text"
    assert content[1]["type"] == "input_image"
    assert content[1]["image_url"].startswith("data:image/png;base64,")
    assert seen_payload["text"]["format"]["type"] == "json_schema"


def test_agent_graph_maps_pipeline_to_role_requirements() -> None:
    graph = _build_agent_graph(
        doc={"source_type": "pptx", "status": "completed"},
        page={
            "layout_blocks": [
                {"id": "vision-1", "source": "openai_vision", "text": "Chart: learning rate overshoots"}
            ],
            "translation_overlay_status": "ready",
        },
        explanation={
            "quality": {"pass": True, "citationRepairAttempted": True},
            "citations": [{"pageNo": 1, "quote": "learning rate"}],
            "scopePages": [1, 2],
        },
        latest_run={"status": "completed", "model_chain": ["deepseek:chat", "openai:gpt-5.4-mini:vision"]},
        vision_model="gpt-5.4-mini",
    )

    node_status = {node["id"]: node["status"] for node in graph["nodes"]}
    assert node_status["vision"] == "completed"
    assert node_status["retrieval"] == "completed"
    assert node_status["quality_gate"] == "completed"
    assert "ReAct" in graph["framework_mapping"]
    assert "Reflexion" in graph["framework_mapping"]
    assert "LangGraph" in graph["framework_mapping"]
    assert "StateGraph" in graph["framework_mapping"]["LangGraph"]
    assert "RAG" in graph["framework_mapping"]


def test_agent_graph_preserves_vision_model_for_page_runs() -> None:
    graph = _build_agent_graph(
        doc={"source_type": "pptx", "status": "completed"},
        page={
            "layout_blocks": [{"id": "vision-1", "source": "openai_vision", "text": "Chart"}],
            "translation_overlay_status": "ready",
        },
        explanation={"quality": {"pass": True}, "citations": [], "scopePages": [2]},
        latest_run={"status": "completed", "model_chain": ["deepseek:deepseek-chat:agent_c"]},
        vision_model="gpt-5.4-mini",
    )

    assert graph["run"]["model_chain"] == [
        "deepseek:deepseek-chat:agent_c",
        "openai:gpt-5.4-mini:vision",
    ]
