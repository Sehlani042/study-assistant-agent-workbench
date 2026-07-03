import pytest

from app.pipeline.agent_c import run_agent_c_with_quality
from app.pipeline.worker import _adapt_workers_after_quality_fail
from app.utils.markdown_math import normalize_math_markdown


class WeirdLLM:
    provider_name = "mock"

    def explain_page(self, context, *, language: str, model_tier: str, feedback=None, instruction=None):
        return {
            "overview": "test",
            "keyPoints": ["a", "b"],
            "conceptLinks": ["x"],
            "formulaBlocks": [],
            "citations": [{"pageNo": context.page_no, "span": "a", "quote": "a"}],
            "confidence": 0.8,
            "teaching": "bad-string-instead-of-object",
            "memoryUsed": {"globalVersion": "v1", "groupId": "g1", "localPages": [1]},
        }


def test_agent_c_handles_non_object_teaching_without_crashing() -> None:
    llm = WeirdLLM()
    page = {
        "page_no": 1,
        "text_content": "a b c",
        "formulas": [],
    }
    global_memory = {"summary": "sum", "keywords": ["a", "b"]}
    group = {"id": "g1", "summary": "group"}
    local_context = [{"page_no": 1, "text": "a"}]

    payload, quality, _ = run_agent_c_with_quality(
        llm_client=llm,
        page=page,
        global_memory=global_memory,
        group=group,
        local_context=local_context,
        language="zh",
        quality_threshold=80,
    )

    assert isinstance(payload.get("teaching"), dict)
    assert "definition" in payload["teaching"]
    assert "score" in quality


def test_math_markdown_normalizer_wraps_common_equation_patterns() -> None:
    text = "模型公式： y_ijk = μ + α_i + β_j + ε_ijk"
    normalized = normalize_math_markdown(text)
    assert "$y_ijk = μ + α_i + β_j + ε_ijk$" in normalized

    bullet = "- y_ijk = μ + α_i + β_j"
    normalized_bullet = normalize_math_markdown(bullet)
    assert normalized_bullet.strip() == "- $y_ijk = μ + α_i + β_j$"

    inline = "下标写法 y_ijk 需要被渲染"
    normalized_inline = normalize_math_markdown(inline)
    assert "$y_ijk$" in normalized_inline


def test_math_markdown_normalizer_handles_factorial_formula_lines() -> None:
    text = (
        "yijkl = μ + αi + βj + γk (主效应)\n"
        "- (αβ)ij + (αγ)ik + (βγ)jk (二向交互作用)\n"
        "- (αβγ)ijk (三向交互作用)\n"
        "- eijkl\n"
        "估计：(αβγ)ijk = yijk• - ŷM(ijk)，其中 ŷM 是包含所有低阶项的预测值。\n"
        "自由度：对于 ABC，df = (a-1)(b-1)(c-1)。"
    )
    normalized = normalize_math_markdown(text)

    assert "$yijkl = μ + αi + βj + γk$ (主效应)" in normalized
    assert "- $(αβ)ij + (αγ)ik + (βγ)jk$ (二向交互作用)" in normalized
    assert "- $(αβγ)ijk$ (三向交互作用)" in normalized
    assert "- $eijkl$" in normalized
    assert "估计：$(αβγ)ijk = yijk• - \\hat{y}M(ijk)$，其中" in normalized
    assert "自由度：对于 ABC，$df = (a-1)(b-1)(c-1)$。" in normalized


def test_math_markdown_normalizer_does_not_wrap_hyphenated_prose() -> None:
    text = 'Alternative: "One-at-a-time" designs, where we vary the levels of a single factor.'
    normalized = normalize_math_markdown(text)
    assert normalized == text
    assert "$" not in normalized


def test_math_markdown_normalizer_maps_hat_and_bar_chars_to_latex() -> None:
    text = "估计：ȳ = ŷM(ijk)"
    normalized = normalize_math_markdown(text)
    assert "估计：$\\bar{y} = \\hat{y}M(ijk)$" in normalized


def test_adaptive_worker_reduces_on_quality_fail_streak() -> None:
    workers = 4
    streak = 0

    workers, streak, reduced = _adapt_workers_after_quality_fail(
        target_workers=workers,
        min_workers=2,
        quality_failed=True,
        quality_fail_streak=streak,
        trigger_streak=2,
    )
    assert workers == 4
    assert streak == 1
    assert reduced is False

    workers, streak, reduced = _adapt_workers_after_quality_fail(
        target_workers=workers,
        min_workers=2,
        quality_failed=True,
        quality_fail_streak=streak,
        trigger_streak=2,
    )
    assert workers == 3
    assert streak == 0
    assert reduced is True

    workers, streak, reduced = _adapt_workers_after_quality_fail(
        target_workers=workers,
        min_workers=2,
        quality_failed=False,
        quality_fail_streak=1,
        trigger_streak=2,
    )
    assert workers == 3
    assert streak == 0
    assert reduced is False


class BudgetAwareLLM:
    provider_name = "mock"

    def __init__(self) -> None:
        self.explain_calls = 0
        self.translate_calls = 0

    def explain_page(self, context, *, language: str, model_tier: str, feedback=None, instruction=None):
        self.explain_calls += 1
        return {
            "overview": "test",
            "keyPoints": ["a"],
            "conceptLinks": [],
            "formulaBlocks": [],
            "citations": [],
            "confidence": 0.5,
            "teaching": {
                "definition": "a",
                "intuition": "a",
                "example": "",
                "focus": "a",
                "pitfall": "",
            },
            "memoryUsed": {"globalVersion": "v1", "groupId": "g1", "localPages": [1]},
        }

    def translate_page_text(self, *, page_text: str, language: str, instruction=None) -> str:
        self.translate_calls += 1
        return "translated"


def test_agent_c_budget_guard_skips_rewrite_and_translation_under_tight_budget() -> None:
    llm = BudgetAwareLLM()
    page = {"page_no": 1, "text_content": "a b c\nd e f", "formulas": []}
    global_memory = {"summary": "sum", "keywords": ["a", "b"]}
    group = {"id": "g1", "summary": "group"}
    local_context = [{"page_no": 1, "text": "a"}]

    payload, quality, model_used = run_agent_c_with_quality(
        llm_client=llm,
        page=page,
        global_memory=global_memory,
        group=group,
        local_context=local_context,
        language="zh",
        quality_threshold=80,
        page_budget_seconds=0.001,
    )

    assert llm.explain_calls == 1
    assert llm.translate_calls == 0
    assert model_used in {"flash", "flash-citation-repair"}
    assert quality["pass"] is False
    assert payload.get("literalTranslation")


def test_agent_c_defers_translation_to_async_backfill_by_default() -> None:
    llm = BudgetAwareLLM()
    page = {"page_no": 1, "text_content": "line1\nline2\nline3", "formulas": []}
    global_memory = {"summary": "sum", "keywords": ["line1", "line2"]}
    group = {"id": "g1", "summary": "group"}
    local_context = [{"page_no": 1, "text": "line1"}]

    payload, quality, _ = run_agent_c_with_quality(
        llm_client=llm,
        page=page,
        global_memory=global_memory,
        group=group,
        local_context=local_context,
        language="zh",
        quality_threshold=80,
        page_budget_seconds=120.0,
    )

    assert llm.explain_calls >= 1
    assert llm.translate_calls == 0
    assert payload.get("literalTranslation")
    assert payload.get("translationStatus") == "pending"
    assert "pass" in quality


class FallbackUnavailableLLM:
    provider_name = "mock"

    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    def explain_page(self, context, *, language: str, model_tier: str, feedback=None, instruction=None):
        self.calls.append((model_tier, bool(feedback)))
        if model_tier == "fallback":
            raise RuntimeError("fallback model unavailable")
        # Keep it intentionally weak so quality path enters rewrite/fallback stages.
        return {
            "overview": "test",
            "keyPoints": ["alpha"],
            "conceptLinks": [],
            "formulaBlocks": [],
            "citations": [],
            "confidence": 0.55,
            "teaching": {
                "definition": "alpha",
                "intuition": "alpha",
                "example": "",
                "focus": "alpha",
                "pitfall": "",
            },
            "memoryUsed": {"globalVersion": "v1", "groupId": "g1", "localPages": [1]},
        }


def test_agent_c_keeps_current_result_when_fallback_model_fails() -> None:
    llm = FallbackUnavailableLLM()
    page = {"page_no": 1, "text_content": "alpha beta gamma delta epsilon zeta eta theta", "formulas": []}
    global_memory = {"summary": "sum", "keywords": ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta"]}
    group = {"id": "g1", "summary": "group"}
    local_context = [{"page_no": 1, "text": "alpha beta gamma"}]

    payload, quality, model_used = run_agent_c_with_quality(
        llm_client=llm,
        page=page,
        global_memory=global_memory,
        group=group,
        local_context=local_context,
        language="zh",
        quality_threshold=95,
    )

    assert any(tier == "fallback" for tier, _ in llm.calls)
    assert model_used in {"flash", "flash-rewrite", "flash-citation-repair"}
    assert isinstance(payload, dict)
    assert "quality" not in payload or isinstance(payload.get("quality"), dict)
    assert "pass" in quality


class LangGraphRetryLLM:
    provider_name = "mock"

    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    def explain_page(self, context, *, language: str, model_tier: str, feedback=None, instruction=None):
        self.calls.append((model_tier, bool(feedback)))
        if feedback is None:
            return {
                "overview": "test",
                "keyPoints": ["alpha"],
                "conceptLinks": [],
                "formulaBlocks": [],
                "citations": [],
                "confidence": 0.35,
                "teaching": {
                    "definition": "alpha",
                    "intuition": "alpha",
                    "example": "",
                    "focus": "alpha",
                    "pitfall": "",
                },
                "memoryUsed": {"globalVersion": "v1", "groupId": "g1", "localPages": [1]},
            }
        return {
            "overview": "这一页解释 learning rate 如何控制 gradient descent 的更新幅度，核心是步长过大会越过最小值。",
            "keyPoints": [
                "learning rate 决定每一步沿 gradient 方向移动多远。",
                "如果步长过大，loss 会在 minimum 附近反复穿越而不是收敛。",
            ],
            "conceptLinks": ["gradient descent", "learning rate", "loss"],
            "formulaBlocks": [],
            "citations": [{"pageNo": context.page_no, "span": "title", "quote": "learning rate gradient descent loss"}],
            "confidence": 0.86,
            "teaching": {
                "definition": "learning rate 是更新公式里的步长系数，决定参数每次沿 gradient 方向移动的距离。",
                "intuition": "它像调旋钮：太小会慢，太大会冲过 minimum。",
                "example": "例如同一条 loss 曲线里，稳定步长逐步下降，而过大步长会在 minimum 两侧跳动。",
                "focus": "本页重点是把 loss 的震荡现象和 learning rate 过大联系起来。",
                "pitfall": "不要只看某一步 loss 下降，要看连续多步是否稳定靠近 minimum。",
            },
            "continuity": {
                "prevBridge": "上一页先说明了 gradient descent 用梯度方向更新参数。",
                "thisPageNew": "本页新增的是 learning rate 过大时会 overshoot。",
                "nextPreview": "下一页可以继续看如何选择更稳定的 step size。",
            },
            "clarity": {
                "conclusion": "结论：loss 在 minimum 附近来回跳，通常说明 learning rate 太大。",
                "steps": [
                    "先看参数更新公式中的 learning rate。",
                    "再看每一步更新是否越过 minimum。",
                    "最后观察 loss 曲线是否震荡。",
                ],
                "example": "例如 loss 从 9 降到 3 后又跳到 7.2，就是过大步长的信号。",
            },
            "microTask": {
                "doNow": "用一句话解释为什么过大的 learning rate 会导致 loss 震荡。",
                "checkQuestion": "如果 loss 先降后升再降，这更像收敛还是 overshoot？",
                "answerHint": "看它是否围绕 minimum 来回穿越。",
            },
            "memoryUsed": {"globalVersion": "v1", "groupId": "g1", "localPages": [1]},
        }


def test_agent_c_records_langgraph_trace_on_quality_retry() -> None:
    llm = LangGraphRetryLLM()
    page = {
        "page_no": 1,
        "text_content": "learning rate gradient descent loss minimum update step overshoot",
        "formulas": [],
    }
    global_memory = {"summary": "gradient descent", "keywords": ["learning", "rate", "gradient", "loss"]}
    group = {"id": "g1", "summary": "optimization"}
    local_context = [{"page_no": 1, "text": "learning rate gradient descent loss"}]

    payload, quality, model_used = run_agent_c_with_quality(
        llm_client=llm,
        page=page,
        global_memory=global_memory,
        group=group,
        local_context=local_context,
        language="zh",
        quality_threshold=70,
        page_budget_seconds=60,
    )

    assert payload["agentFramework"] == "LangGraph"
    assert payload["agentGraphTrace"]["framework"] == "LangGraph"
    assert "agent_c_draft" in payload["agentGraphTrace"]["nodes"]
    assert "quality_gate" in payload["agentGraphTrace"]["nodes"]
    assert "reflection_retry" in payload["agentGraphTrace"]["nodes"]
    assert payload["agentGraphTrace"]["quality_pass"] is quality["pass"]
    assert model_used in {"flash-rewrite", "fallback", "flash-citation-repair", "flash"}
    assert len(llm.calls) >= 2
