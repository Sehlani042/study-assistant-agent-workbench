from __future__ import annotations

from typing import Any


LEARNER_LEVELS = {"beginner", "intermediate", "advanced"}
LEARNING_GOALS = {"understand", "learn_and_apply", "exam"}
DEPTH_MODES = {"quick", "standard", "deep"}
ATTENTION_SUPPORT_MODES = {"standard", "adhd_friendly"}


def default_learning_preferences() -> dict[str, str]:
    return {
        "learner_level": "beginner",
        "learning_goal": "understand",
        "depth_mode": "standard",
        "attention_support": "adhd_friendly",
    }


def normalize_learning_preferences(raw: dict[str, Any] | None = None) -> dict[str, str]:
    defaults = default_learning_preferences()
    payload = raw if isinstance(raw, dict) else {}
    learner_level = str(payload.get("learner_level", defaults["learner_level"])).strip().lower()
    learning_goal = str(payload.get("learning_goal", defaults["learning_goal"])).strip().lower()
    depth_mode = str(payload.get("depth_mode", defaults["depth_mode"])).strip().lower()
    attention_support = str(payload.get("attention_support", defaults["attention_support"])).strip().lower()
    return {
        "learner_level": learner_level if learner_level in LEARNER_LEVELS else defaults["learner_level"],
        "learning_goal": learning_goal if learning_goal in LEARNING_GOALS else defaults["learning_goal"],
        "depth_mode": depth_mode if depth_mode in DEPTH_MODES else defaults["depth_mode"],
        "attention_support": (
            attention_support if attention_support in ATTENTION_SUPPORT_MODES else defaults["attention_support"]
        ),
    }


def build_learning_prompt_suffix(profile: dict[str, Any] | None) -> str:
    prefs = normalize_learning_preferences(profile)
    level_map = {
        "beginner": "默认把术语拆开解释，不假设读者已经熟练掌握前置知识。",
        "intermediate": "默认保留关键术语，但仍要补足跳步位置。",
        "advanced": "默认可以更紧凑，但不要牺牲关键推导节点。",
    }
    goal_map = {
        "understand": "目标是先讲懂：优先解释概念含义和页内逻辑。",
        "learn_and_apply": "目标是学会应用：解释后给出可操作步骤或使用场景。",
        "exam": "目标是考试掌握：突出考点、易错点和最常见的判断线索。",
    }
    depth_map = {
        "quick": "深度模式为 quick：压缩到最核心的结论、三步讲解和一个最小例子。",
        "standard": "深度模式为 standard：结论、三步讲解、例子和一个立即可做的小任务都要保留。",
        "deep": "深度模式为 deep：在不失焦的前提下，把关键推导或概念依赖讲完整。",
    }
    attention_map = {
        "standard": "注意力支持为 standard：保持结构清楚，但不过度拆分。",
        "adhd_friendly": "注意力支持为 adhd_friendly：短句、先结论后细节、分块呈现，避免长段落和堆术语。",
    }
    return "\n".join(
        [
            "学习参数（本次任务固定生效）：",
            f"- learner_level: {prefs['learner_level']}；{level_map[prefs['learner_level']]}",
            f"- learning_goal: {prefs['learning_goal']}；{goal_map[prefs['learning_goal']]}",
            f"- depth_mode: {prefs['depth_mode']}；{depth_map[prefs['depth_mode']]}",
            f"- attention_support: {prefs['attention_support']}；{attention_map[prefs['attention_support']]}",
        ]
    )


def apply_learning_profile_to_prompt_config(
    prompt_config: dict[str, str],
    *,
    learning_profile: dict[str, Any] | None,
) -> dict[str, str]:
    suffix = build_learning_prompt_suffix(learning_profile)
    out = dict(prompt_config)
    for key in ("agent_c_instruction", "chat_instruction", "formula_instruction"):
        base = str(out.get(key, "")).strip()
        out[key] = f"{base}\n{suffix}".strip()
    return out


def build_translation_instruction(profile: dict[str, Any] | None) -> str:
    prefs = normalize_learning_preferences(profile)
    suffix = build_learning_prompt_suffix(prefs)
    return (
        "只做忠实直译，不做教学解释。尽量保留原页面的标题、列表、表格、公式块和段落层级；"
        "Markdown 与 LaTeX 必须保持可渲染。\n"
        f"{suffix}"
    )
