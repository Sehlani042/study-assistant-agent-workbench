from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.llm.mock_client import MockLLMClient
from app.llm.openai_client import OpenAIClient
from app.main import create_app


def _build_pdf(path: Path, pages: int = 2) -> None:
    c = canvas.Canvas(str(path), pagesize=letter)
    for i in range(1, pages + 1):
        c.drawString(100, 720, f"OpenAI compat test page {i}")
        c.showPage()
    c.save()


def _wait_for_completion(
    client: TestClient,
    document_id: str,
    timeout_s: float = 20.0,
    token: str | None = None,
) -> dict:
    deadline = time.time() + timeout_s
    payload = None
    headers = {"Authorization": f"Bearer {token}"} if token else None
    while time.time() < deadline:
        resp = client.get(f"/api/v1/documents/{document_id}", headers=headers)
        assert resp.status_code == 200
        payload = resp.json()
        if payload["status"] in {"completed", "failed", "canceled"}:
            return payload
        time.sleep(0.2)
    raise AssertionError(f"document {document_id} did not finish in time: {payload}")


@pytest.fixture()
def openai_auth_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    data_dir = tmp_path / "data"
    db_path = tmp_path / "assistant-openai.db"
    env = {
        "DATA_DIR": str(data_dir),
        "DATABASE_URL": f"sqlite:///{db_path}",
        "AUTH_ENABLED": "true",
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD": "UnitTestAdmin123",
        "LLM_PROVIDER": "openai",
        "OPENAI_API_KEY": "unit-test-openai-shared-key",
        "OPENAI_MODEL": "gpt-5.2",
        "GEMINI_API_KEY": "unit-test-gemini-shared-key",
        "GEMINI_FLASH_MODEL": "gemini-flash-latest",
        "LLM_KEY_ENCRYPTION_SECRET": "unit-test-encryption-secret",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return env


def _login(client: TestClient, username: str, password: str) -> tuple[str, dict]:
    login = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200
    payload = login.json()
    return payload["access_token"], payload["user"]


def _create_user(client: TestClient, admin_token: str, username: str, password: str) -> None:
    created = client.post(
        "/api/v1/auth/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"username": username, "password": password, "role": "user"},
    )
    assert created.status_code == 200


def test_personal_key_is_encrypted_and_persists_after_restart(openai_auth_env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app()
    client = TestClient(app)
    admin_token, _admin = _login(client, "admin", "UnitTestAdmin123")
    _create_user(client, admin_token, "openai_u1", "openai_u1_pass_123")
    user_token, user_payload = _login(client, "openai_u1", "openai_u1_pass_123")
    user_id = str(user_payload["id"])

    access_before = client.get("/api/v1/auth/llm/access", headers={"Authorization": f"Bearer {user_token}"})
    assert access_before.status_code == 200
    before_payload = access_before.json()
    assert "providers" in before_payload
    assert "openai" in before_payload["providers"]
    assert before_payload["providers"]["openai"]["has_personal_key"] is False

    save_key = client.post(
        "/api/v1/auth/llm/key",
        headers={"Authorization": f"Bearer {user_token}"},
        json={"provider": "openai", "api_key": "unit-test-openai-personal-key"},
    )
    assert save_key.status_code == 200
    assert save_key.json()["saved"] is True

    row = client.app.state.container.store.db.fetchone(
        "SELECT encrypted_key FROM user_llm_keys WHERE user_id = ? AND provider = ?",
        (user_id, "openai"),
    )
    assert row is not None
    encrypted = str(row["encrypted_key"])
    assert encrypted
    assert "unit-test-openai-personal-key" not in encrypted

    access_after = client.get("/api/v1/auth/llm/access", headers={"Authorization": f"Bearer {user_token}"})
    assert access_after.status_code == 200
    assert access_after.json()["providers"]["openai"]["has_personal_key"] is True

    # Recreate app with same DB and encryption secret to validate restart persistence.
    app2 = create_app()
    client2 = TestClient(app2)
    user_token2, _ = _login(client2, "openai_u1", "openai_u1_pass_123")
    access_after_restart = client2.get("/api/v1/auth/llm/access", headers={"Authorization": f"Bearer {user_token2}"})
    assert access_after_restart.status_code == 200
    assert access_after_restart.json()["providers"]["openai"]["has_personal_key"] is True


def test_task_override_beats_user_default_provider_and_model(
    openai_auth_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    seen_calls: list[tuple[str, str, str]] = []

    def fake_build_provider_client(*, provider: str, settings, api_key: str | None = None, model: str | None = None):
        seen_calls.append((provider, str(model or ""), str(api_key or "")))
        return MockLLMClient()

    monkeypatch.setattr("app.llm.access.build_provider_client", fake_build_provider_client)
    monkeypatch.setattr("app.llm.provider.build_provider_client", fake_build_provider_client)

    app = create_app()
    client = TestClient(app)
    admin_token, _ = _login(client, "admin", "UnitTestAdmin123")
    _create_user(client, admin_token, "route_u1", "route_u1_pass_123")
    user_token, _ = _login(client, "route_u1", "route_u1_pass_123")

    # Save both providers' personal keys.
    for provider, key in (
        ("openai", "unit-test-openai-route-key"),
        ("gemini", "unit-test-gemini-route-key"),
    ):
        saved = client.post(
            "/api/v1/auth/llm/key",
            headers={"Authorization": f"Bearer {user_token}"},
            json={"provider": provider, "api_key": key},
        )
        assert saved.status_code == 200

    # User default set to OpenAI gpt-5.2.
    updated = client.put(
        "/api/v1/settings/llm",
        headers={"Authorization": f"Bearer {user_token}"},
        json={"provider": "openai", "model": "gpt-5.2", "scope": "user"},
    )
    assert updated.status_code == 200
    assert updated.json()["effective"]["provider"] == "openai"
    assert updated.json()["effective"]["model"] == "gpt-5.2"

    pdf_path = tmp_path / "override.pdf"
    _build_pdf(pdf_path)

    # Upload with task override -> should use gemini override, not user default openai.
    with pdf_path.open("rb") as f:
        upload = client.post(
            "/api/v1/documents",
            headers={"Authorization": f"Bearer {user_token}"},
            files={"file": (pdf_path.name, f, "application/pdf")},
            data={"llm_provider": "gemini", "llm_model": "gemini-flash-latest"},
        )
    assert upload.status_code == 200
    document_id = upload.json()["document_id"]
    assert seen_calls, "expected llm client builder to be called"
    assert seen_calls[-1][0] == "gemini"
    assert seen_calls[-1][1] == "gemini-flash-latest"

    status = _wait_for_completion(client, document_id, token=user_token)
    assert status["status"] in {"completed", "failed"}

    # Regenerate without task override -> should fall back to user default openai gpt-5.2.
    regen = client.post(
        f"/api/v1/documents/{document_id}/pages/1/regenerate?language=zh",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert regen.status_code == 200
    assert seen_calls[-1][0] == "openai"
    assert seen_calls[-1][1] == "gpt-5.2"


def test_openai_model_unavailable_fails_strictly(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BadResponse:
        status_code = 404
        text = '{"error":{"message":"The model `gpt-5.2` does not exist"}}'

        def raise_for_status(self) -> None:
            request = httpx.Request("POST", "https://api.openai.com/v1/responses")
            raise httpx.HTTPStatusError("404 model not found", request=request, response=self)

        def json(self) -> dict:
            return {"error": {"message": "The model `gpt-5.2` does not exist"}}

    class _FakeHttpxClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, *args, **kwargs):
            return _BadResponse()

    monkeypatch.setattr("app.llm.openai_client.httpx.Client", _FakeHttpxClient)

    client = OpenAIClient(api_key="unit-test-openai-key", model="gpt-5.2")
    with pytest.raises(RuntimeError) as exc:
        client.summarize_document([{"page_no": 1, "text_content": "hello"}])
    msg = str(exc.value).lower()
    assert "gpt-5.2" in msg
    assert "404" in msg


def test_fallback_chain_settings_user_overrides_global(openai_auth_env: dict[str, str]) -> None:
    app = create_app()
    client = TestClient(app)
    admin_token, _ = _login(client, "admin", "UnitTestAdmin123")
    _create_user(client, admin_token, "fallback_u1", "fallback_u1_pass_123")
    user_token, _ = _login(client, "fallback_u1", "fallback_u1_pass_123")

    get_initial = client.get(
        "/api/v1/settings/llm/fallback-chain",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert get_initial.status_code == 200
    initial_payload = get_initial.json()
    assert isinstance(initial_payload.get("global_default"), list)
    assert isinstance(initial_payload.get("user_default"), list)
    assert isinstance(initial_payload.get("effective"), list)
    assert initial_payload.get("source") in {"global", "user"}

    global_chain = ["gemini:flash", "gemini:pro", "openai:gpt-5.2-mini"]
    set_global = client.put(
        "/api/v1/settings/llm/fallback-chain",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"scope": "global", "chain": global_chain},
    )
    assert set_global.status_code == 200

    get_after_global = client.get(
        "/api/v1/settings/llm/fallback-chain",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert get_after_global.status_code == 200
    payload_after_global = get_after_global.json()
    assert payload_after_global["global_default"] == global_chain
    assert payload_after_global["effective"] == global_chain
    assert payload_after_global["source"] == "global"

    user_chain = ["gemini:flash", "openai:gpt-5.2-mini"]
    set_user = client.put(
        "/api/v1/settings/llm/fallback-chain",
        headers={"Authorization": f"Bearer {user_token}"},
        json={"scope": "user", "chain": user_chain},
    )
    assert set_user.status_code == 200

    get_after_user = client.get(
        "/api/v1/settings/llm/fallback-chain",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert get_after_user.status_code == 200
    payload_after_user = get_after_user.json()
    assert payload_after_user["global_default"] == global_chain
    assert payload_after_user["user_default"] == user_chain
    assert payload_after_user["effective"] == user_chain
    assert payload_after_user["source"] == "user"
