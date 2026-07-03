from __future__ import annotations

import json
from pathlib import Path

import httpx

from app.config import get_settings
from app.llm.deepseek_client import DeepSeekClient
from app.llm.provider import (
    build_provider_client,
    default_model_for_provider,
    recommended_models_for_provider,
    shared_api_key_for_provider,
)


def test_settings_loads_deepseek_key_from_secret_path(tmp_path: Path, monkeypatch) -> None:
    key_path = tmp_path / "deepseek_api_key.txt"
    key_path.write_text("fake-deepseek-key-from-local-secret\n", encoding="utf-8")

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'assistant.db'}")
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY_PATH", str(key_path))

    settings = get_settings()

    assert settings.llm_provider == "deepseek"
    assert settings.deepseek_api_key == "fake-deepseek-key-from-local-secret"
    assert settings.deepseek_model == "deepseek-chat"
    assert shared_api_key_for_provider(settings=settings, provider="deepseek") == "fake-deepseek-key-from-local-secret"
    assert default_model_for_provider(settings=settings, provider="deepseek") == "deepseek-chat"


def test_provider_builds_deepseek_client_from_settings(tmp_path: Path, monkeypatch) -> None:
    key_path = tmp_path / "deepseek_api_key.txt"
    key_path.write_text("fake-deepseek-provider-secret\n", encoding="utf-8")

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'assistant.db'}")
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY_PATH", str(key_path))
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-chat")

    settings = get_settings()
    client = build_provider_client(provider="deepseek", settings=settings)

    assert isinstance(client, DeepSeekClient)
    assert client.model == "deepseek-chat"
    recommended = recommended_models_for_provider(settings=settings, provider="deepseek")
    assert [item["id"] for item in recommended] == ["deepseek-chat", "deepseek-reasoner"]


def test_deepseek_client_uses_chat_completions_and_parses_json(monkeypatch) -> None:
    seen: dict[str, object] = {}

    class _Response:
        status_code = 200
        text = json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"summary": "ok", "keywords": [], "glossary": [], "knowledge_map": [], "learning_arc": [], "groups": []}
                            )
                        }
                    }
                ]
            }
        )

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return json.loads(self.text)

    class _FakeHttpxClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url: str, *, headers: dict, json: dict):
            seen["url"] = url
            seen["headers"] = headers
            seen["json"] = json
            return _Response()

    monkeypatch.setattr("app.llm.deepseek_client.httpx.Client", _FakeHttpxClient)

    client = DeepSeekClient(api_key="fake-deepseek-test-key", model="deepseek-chat")
    payload = client.summarize_document([{"page_no": 1, "text_content": "Gradient descent"}])

    assert payload["summary"] == "ok"
    assert seen["url"] == "https://api.deepseek.com/chat/completions"
    sent = seen["json"]
    assert sent["model"] == "deepseek-chat"
    assert sent["response_format"] == {"type": "json_object"}
    assert sent["messages"][0]["role"] == "system"
    assert "Authorization" in seen["headers"]
