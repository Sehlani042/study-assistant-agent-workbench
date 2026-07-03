from app.prompts import default_prompt_config


def test_default_prompt_is_markdown_math_and_adhd_friendly() -> None:
    cfg = default_prompt_config()
    agent_c = cfg["agent_c_instruction"]
    chat = cfg["chat_instruction"]

    assert "ADHD" in agent_c
    assert "$...$" in agent_c
    assert "$$...$$" in agent_c
    assert "Markdown" in agent_c

    assert "ADHD" in chat
    assert "$...$" in chat
