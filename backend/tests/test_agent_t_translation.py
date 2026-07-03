from app.pipeline.agent_t import run_agent_t_translation, translate_layout_blocks
from app.pipeline.formulas import looks_like_formula_candidate


class _DummyLLM:
    provider_name = "dummy"

    def __init__(self, output: str) -> None:
        self.output = output

    def translate_page_text(self, *, page_text: str, language: str, instruction: str | None = None) -> str:
        return self.output


class _FailingLLM:
    provider_name = "failing"

    def translate_page_text(self, *, page_text: str, language: str, instruction: str | None = None) -> str:
        raise RuntimeError("expired key")


def test_agent_t_translation_normalizes_layout_and_math() -> None:
    raw = """
标题
• 第一条
2) 第二条
| 列1| 列2 |
|---|:--|
a_ij = b
```
for i in range(3):
  print(i)
```
"""
    llm = _DummyLLM(raw)
    out = run_agent_t_translation(
        llm_client=llm,
        page_text="ignored",
        language="zh",
    )

    assert "- 第一条" in out
    assert "2. 第二条" in out
    assert "| 列1 | 列2 |" in out
    assert "| --- | :-- |" in out
    assert "$a_ij = b$" in out
    assert "```" in out


def test_translate_layout_blocks_keeps_title_granularity() -> None:
    llm = _DummyLLM(
        """
### 什么是纵向数据 (Longitudinal Data)？

**结论：**
纵向数据是指在多个时间点对同一对象进行重复观测所收集的数据。

**三步讲解：**
1. 第一条
2. 第二条
"""
    )
    translation_blocks, untranslated_blocks, literal_translation, overlay_status = translate_layout_blocks(
        llm_client=llm,
        layout_blocks=[
            {
                "id": "pdf-1",
                "text": "What is Longitudinal Data?",
                "bbox": {"x": 0.02, "y": 0.03, "width": 0.44, "height": 0.05},
                "kind": "title",
                "source": "pdf_text",
                "confidence": 1.0,
                "font_size": 14.0,
                "reading_order": 1,
            }
        ],
        language="zh",
    )

    assert untranslated_blocks == []
    assert overlay_status == "ready"
    assert len(translation_blocks) == 1
    assert "三步讲解" not in translation_blocks[0]["text"]
    assert "例子" not in translation_blocks[0]["text"]
    assert translation_blocks[0]["text"].strip() == "什么是纵向数据 (Longitudinal Data)？"
    assert literal_translation.strip() == "什么是纵向数据 (Longitudinal Data)？"


def test_formula_detection_skips_long_hyphenated_heading() -> None:
    assert looks_like_formula_candidate("Cross-Sectional vs. Longitudinal:") is False


def test_formula_detection_skips_prose_sentence_with_single_symbol() -> None:
    text = "Cross-Sectional: We measure m different subjects exactly once."
    assert looks_like_formula_candidate(text) is False


def test_formula_detection_skips_beamer_footer_with_page_marker() -> None:
    text = "Tianxi Li (University of Minnesota)\nSTAT 8052\nSpring 2026\n2 / 50"
    assert looks_like_formula_candidate(text) is False


def test_translate_layout_blocks_marks_failed_translation_instead_of_echoing_source() -> None:
    translation_blocks, untranslated_blocks, literal_translation, overlay_status = translate_layout_blocks(
        llm_client=_FailingLLM(),
        layout_blocks=[
            {
                "id": "pdf-1",
                "text": "What is Longitudinal Data?",
                "bbox": {"x": 0.02, "y": 0.03, "width": 0.44, "height": 0.05},
                "kind": "title",
                "source": "pdf_text",
                "confidence": 1.0,
                "font_size": 14.0,
                "reading_order": 1,
            }
        ],
        language="zh",
    )

    assert translation_blocks == []
    assert literal_translation == ""
    assert overlay_status == "unavailable"
    assert untranslated_blocks[0]["reason"] == "translation_failed"


def test_translate_layout_blocks_rejects_unchanged_english_output_for_chinese() -> None:
    translation_blocks, untranslated_blocks, literal_translation, overlay_status = translate_layout_blocks(
        llm_client=_DummyLLM("What is Longitudinal Data?"),
        layout_blocks=[
            {
                "id": "pdf-1",
                "text": "What is Longitudinal Data?",
                "bbox": {"x": 0.02, "y": 0.03, "width": 0.44, "height": 0.05},
                "kind": "title",
                "source": "pdf_text",
                "confidence": 1.0,
                "font_size": 14.0,
                "reading_order": 1,
            }
        ],
        language="zh",
    )

    assert translation_blocks == []
    assert literal_translation == ""
    assert overlay_status == "unavailable"
    assert untranslated_blocks[0]["reason"] == "translation_failed"
