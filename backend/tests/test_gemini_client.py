import pytest
import httpx

from app.llm.gemini_client import GeminiClient


def test_gemini_fallback_tier_tries_multiple_model_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    client = GeminiClient(api_key="test-key", flash_model="gemini-flash-latest", fallback_model="gemini-2.5-pro")

    # Keep candidate filtering deterministic for this test.
    monkeypatch.setattr(client, "_discover_available_models", lambda: None)

    attempted_urls: list[str] = []

    class FakeHttpClient:
        def __init__(self, *args, **kwargs) -> None:  # noqa: D401
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url: str, headers=None, json=None):
            attempted_urls.append(url)
            request = httpx.Request("POST", url)
            response = httpx.Response(status_code=404, request=request, text="model not found")
            raise httpx.HTTPStatusError("not found", request=request, response=response)

    monkeypatch.setattr(httpx, "Client", FakeHttpClient)

    with pytest.raises(RuntimeError):
        client._generate_json(
            model="gemini-2.5-pro",
            prompt="{}",
            tier="fallback",
            schema=None,
        )

    assert len(attempted_urls) >= 2


def test_gemini_flash_lite_prefers_discovered_31_model(monkeypatch: pytest.MonkeyPatch) -> None:
    client = GeminiClient(
        api_key="test-key",
        flash_model="gemini-3.1-flash-lite",
        fallback_model="gemini-2.5-pro",
    )
    monkeypatch.setattr(
        client,
        "_discover_available_models",
        lambda: {
            "gemini-2.5-flash-lite",
            "gemini-3.1-flash-lite-preview-02-2026",
            "gemini-2.5-pro",
        },
    )

    candidates = client._candidate_models(model="gemini-3.1-flash-lite", tier="flash")

    assert candidates[0] == "gemini-3.1-flash-lite-preview-02-2026"
    assert "gemini-2.5-flash-lite" in candidates


def test_gemini_flash_lite_falls_back_to_25_lite_when_31_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    client = GeminiClient(
        api_key="test-key",
        flash_model="gemini-3.1-flash-lite",
        fallback_model="gemini-2.5-pro",
    )
    monkeypatch.setattr(
        client,
        "_discover_available_models",
        lambda: {"gemini-2.5-flash-lite", "gemini-2.5-pro"},
    )

    candidates = client._candidate_models(model="gemini-3.1-flash-lite", tier="flash")

    assert candidates[0] == "gemini-2.5-flash-lite"
