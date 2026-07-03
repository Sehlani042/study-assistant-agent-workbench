from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.main import create_app


@pytest.fixture()
def auth_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'assistant.db'}")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "UnitTestAdmin123")
    app = create_app()
    return TestClient(app)


def _build_pdf(path: Path, pages: int = 1) -> None:
    c = canvas.Canvas(str(path), pagesize=letter)
    for i in range(1, pages + 1):
        c.drawString(100, 720, f"Auth flow test page {i}")
        c.showPage()
    c.save()


def test_admin_login_and_me(auth_client: TestClient) -> None:
    login = auth_client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "UnitTestAdmin123"},
    )
    assert login.status_code == 200
    payload = login.json()
    token = payload["access_token"]
    assert token
    assert payload["user"]["role"] == "admin"

    me = auth_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me.status_code == 200
    assert me.json()["username"] == "admin"


def test_auth_policy_endpoint(auth_client: TestClient) -> None:
    resp = auth_client.get("/api/v1/auth/policy")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["username"]["pattern"] == "^[a-z][a-z0-9_.-]{2,31}$"
    assert payload["username"]["min_length"] == 3
    assert payload["username"]["max_length"] == 32
    assert payload["password"]["min_length"] == 8
    assert payload["password"]["max_length"] == 128
    assert payload["password"]["require_letters"] is True
    assert payload["password"]["require_numbers"] is True
    assert payload["password"]["forbid_whitespace"] is True
    assert payload["registration"]["mode"] == "open"
    assert payload["registration"]["email_code_resend_seconds"] == 60


def test_register_user_open_mode(auth_client: TestClient) -> None:
    created = auth_client.post(
        "/api/v1/auth/register",
        json={"username": "newbie", "password": "newbie123"},
    )
    assert created.status_code == 200
    assert created.json()["username"] == "newbie"

    login = auth_client.post(
        "/api/v1/auth/login",
        json={"username": "newbie", "password": "newbie123"},
    )
    assert login.status_code == 200


@pytest.fixture()
def auth_client_registration_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'assistant-closed.db'}")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_REGISTRATION_MODE", "closed")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "UnitTestAdmin123")
    app = create_app()
    return TestClient(app)


def test_register_user_closed_mode_rejected(auth_client_registration_closed: TestClient) -> None:
    denied = auth_client_registration_closed.post(
        "/api/v1/auth/register",
        json={"username": "blocked", "password": "blocked123"},
    )
    assert denied.status_code == 403


@pytest.fixture()
def auth_client_registration_invite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'assistant-invite.db'}")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_REGISTRATION_MODE", "invite")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "UnitTestAdmin123")
    app = create_app()
    return TestClient(app)


def test_register_user_invite_mode(auth_client_registration_invite: TestClient) -> None:
    denied = auth_client_registration_invite.post(
        "/api/v1/auth/register",
        json={"username": "invitee", "password": "invitee123"},
    )
    assert denied.status_code == 403

    admin_login = auth_client_registration_invite.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "UnitTestAdmin123"},
    )
    assert admin_login.status_code == 200
    admin_token = admin_login.json()["access_token"]
    created_invite = auth_client_registration_invite.post(
        "/api/v1/auth/registration-invites",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"ttl_hours": 24, "max_uses": 2, "note": "test"},
    )
    assert created_invite.status_code == 200
    invite_code = created_invite.json()["code"]
    assert invite_code

    accepted = auth_client_registration_invite.post(
        "/api/v1/auth/register",
        json={"username": "invitee", "password": "invitee123", "invite_code": invite_code},
    )
    assert accepted.status_code == 200
    assert accepted.json()["username"] == "invitee"


@pytest.fixture()
def auth_client_registration_email(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'assistant-email.db'}")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_REGISTRATION_MODE", "open")
    monkeypatch.setenv("AUTH_EMAIL_VERIFICATION_REQUIRED", "true")
    monkeypatch.setenv("AUTH_EMAIL_CODE_RESEND_SECONDS", "60")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "UnitTestAdmin123")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_FROM", "noreply@example.com")
    monkeypatch.setattr("app.api.routes_auth._generate_email_code", lambda: "123456")
    monkeypatch.setattr("app.api.routes_auth.send_verification_email", lambda *args, **kwargs: None)
    app = create_app()
    return TestClient(app)


def test_register_with_email_verification(auth_client_registration_email: TestClient) -> None:
    without_email = auth_client_registration_email.post(
        "/api/v1/auth/register",
        json={"username": "mailuser", "password": "mailuser123"},
    )
    assert without_email.status_code == 400

    sent = auth_client_registration_email.post(
        "/api/v1/auth/register/email-code",
        json={"email": "mailuser@example.com"},
    )
    assert sent.status_code == 200
    assert sent.json()["sent"] is True
    assert sent.json()["resend_after_seconds"] == 60

    wrong_code = auth_client_registration_email.post(
        "/api/v1/auth/register",
        json={
            "username": "mailuser",
            "password": "mailuser123",
            "email": "mailuser@example.com",
            "email_code": "000000",
        },
    )
    assert wrong_code.status_code == 403

    accepted = auth_client_registration_email.post(
        "/api/v1/auth/register",
        json={
            "username": "mailuser",
            "password": "mailuser123",
            "email": "mailuser@example.com",
            "email_code": "123456",
        },
    )
    assert accepted.status_code == 200
    payload = accepted.json()
    assert payload["username"] == "mailuser"


def test_register_email_code_resend_cooldown(auth_client_registration_email: TestClient) -> None:
    first = auth_client_registration_email.post(
        "/api/v1/auth/register/email-code",
        json={"email": "cooldown@example.com"},
    )
    assert first.status_code == 200
    assert first.json()["resend_after_seconds"] == 60

    second = auth_client_registration_email.post(
        "/api/v1/auth/register/email-code",
        json={"email": "cooldown@example.com"},
    )
    assert second.status_code == 429
    assert "wait" in second.text.lower()


def test_admin_can_create_user(auth_client: TestClient) -> None:
    login = auth_client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "UnitTestAdmin123"},
    )
    token = login.json()["access_token"]
    created = auth_client.post(
        "/api/v1/auth/users",
        headers={"Authorization": f"Bearer {token}"},
        json={"username": "alice", "password": "alice-pass-123", "role": "user"},
    )
    assert created.status_code == 200
    assert created.json()["username"] == "alice"


def test_admin_can_update_user_permissions(auth_client: TestClient) -> None:
    login = auth_client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "UnitTestAdmin123"},
    )
    token = login.json()["access_token"]
    created = auth_client.post(
        "/api/v1/auth/users",
        headers={"Authorization": f"Bearer {token}"},
        json={"username": "priv1", "password": "priv-pass-123", "role": "user"},
    )
    assert created.status_code == 200
    user_id = created.json()["id"]

    updated = auth_client.patch(
        f"/api/v1/auth/users/{user_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "is_active": True,
            "can_use_shared_key": True,
            "permissions": {
                "can_manage_accounts": True,
                "can_manage_prompts": False,
                "can_manage_shared_keys": True,
            },
        },
    )
    assert updated.status_code == 200
    payload = updated.json()
    assert payload["can_use_shared_key"] is True
    assert payload["permissions"]["can_manage_accounts"] is True
    assert payload["permissions"]["can_manage_prompts"] is False
    assert payload["permissions"]["can_manage_shared_keys"] is True


def test_non_admin_cannot_update_user_permissions(auth_client: TestClient) -> None:
    admin_login = auth_client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "UnitTestAdmin123"},
    )
    admin_token = admin_login.json()["access_token"]

    created = auth_client.post(
        "/api/v1/auth/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"username": "normal1", "password": "normal-pass-123", "role": "user"},
    )
    assert created.status_code == 200

    normal_login = auth_client.post(
        "/api/v1/auth/login",
        json={"username": "normal1", "password": "normal-pass-123"},
    )
    assert normal_login.status_code == 200
    normal_token = normal_login.json()["access_token"]
    normal_user_id = normal_login.json()["user"]["id"]

    denied = auth_client.patch(
        f"/api/v1/auth/users/{normal_user_id}",
        headers={"Authorization": f"Bearer {normal_token}"},
        json={"permissions": {"can_manage_prompts": True}},
    )
    assert denied.status_code == 403


def test_document_access_isolated_by_owner(auth_client: TestClient, tmp_path: Path) -> None:
    admin_login = auth_client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "UnitTestAdmin123"},
    )
    assert admin_login.status_code == 200
    admin_token = admin_login.json()["access_token"]

    auth_client.post(
        "/api/v1/auth/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"username": "userone", "password": "user1pass123", "role": "user"},
    )
    auth_client.post(
        "/api/v1/auth/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"username": "usertwo", "password": "user2pass123", "role": "user"},
    )

    u1_login = auth_client.post(
        "/api/v1/auth/login",
        json={"username": "userone", "password": "user1pass123"},
    )
    u2_login = auth_client.post(
        "/api/v1/auth/login",
        json={"username": "usertwo", "password": "user2pass123"},
    )
    assert u1_login.status_code == 200
    assert u2_login.status_code == 200
    u1_token = u1_login.json()["access_token"]
    u2_token = u2_login.json()["access_token"]

    pdf_path = tmp_path / "owner-scope.pdf"
    _build_pdf(pdf_path, pages=1)
    with pdf_path.open("rb") as f:
        uploaded = auth_client.post(
            "/api/v1/documents",
            headers={"Authorization": f"Bearer {u1_token}"},
            files={"file": (pdf_path.name, f, "application/pdf")},
        )
    assert uploaded.status_code == 200
    document_id = uploaded.json()["document_id"]

    u1_docs = auth_client.get("/api/v1/documents", headers={"Authorization": f"Bearer {u1_token}"})
    assert u1_docs.status_code == 200
    assert any(item["document_id"] == document_id for item in u1_docs.json()["items"])

    u2_docs = auth_client.get("/api/v1/documents", headers={"Authorization": f"Bearer {u2_token}"})
    assert u2_docs.status_code == 200
    assert all(item["document_id"] != document_id for item in u2_docs.json()["items"])

    u2_status = auth_client.get(f"/api/v1/documents/{document_id}", headers={"Authorization": f"Bearer {u2_token}"})
    assert u2_status.status_code == 404


def test_admin_create_user_rejects_invalid_identity(auth_client: TestClient) -> None:
    login = auth_client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "UnitTestAdmin123"},
    )
    token = login.json()["access_token"]

    bad_username = auth_client.post(
        "/api/v1/auth/users",
        headers={"Authorization": f"Bearer {token}"},
        json={"username": "A!", "password": "alice-pass-123", "role": "user"},
    )
    assert bad_username.status_code == 400
    assert "username" in str(bad_username.json().get("detail", "")).lower()

    bad_password = auth_client.post(
        "/api/v1/auth/users",
        headers={"Authorization": f"Bearer {token}"},
        json={"username": "alice2", "password": "abcdefgh", "role": "user"},
    )
    assert bad_password.status_code == 400
    assert "password" in str(bad_password.json().get("detail", "")).lower()


def test_prompt_isolation_and_reset_per_user(auth_client: TestClient) -> None:
    admin_login = auth_client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "UnitTestAdmin123"},
    )
    assert admin_login.status_code == 200
    admin_token = admin_login.json()["access_token"]

    created = auth_client.post(
        "/api/v1/auth/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"username": "bob", "password": "bob-pass-123", "role": "user"},
    )
    assert created.status_code == 200

    bob_login = auth_client.post(
        "/api/v1/auth/login",
        json={"username": "bob", "password": "bob-pass-123"},
    )
    assert bob_login.status_code == 200
    bob_token = bob_login.json()["access_token"]

    bob_cfg_before = auth_client.get(
        "/api/v1/settings/prompt",
        headers={"Authorization": f"Bearer {bob_token}"},
    )
    assert bob_cfg_before.status_code == 200
    assert bob_cfg_before.json()["has_custom"] is False

    bob_updated = auth_client.put(
        "/api/v1/settings/prompt",
        headers={"Authorization": f"Bearer {bob_token}"},
        json={"agent_c_instruction": "bob custom prompt"},
    )
    assert bob_updated.status_code == 200
    assert bob_updated.json()["has_custom"] is True
    assert "bob custom prompt" in bob_updated.json()["agent_c_instruction"]

    admin_cfg = auth_client.get(
        "/api/v1/settings/prompt",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert admin_cfg.status_code == 200
    assert "bob custom prompt" not in admin_cfg.json()["agent_c_instruction"]

    bob_reset = auth_client.post(
        "/api/v1/settings/prompt/reset",
        headers={"Authorization": f"Bearer {bob_token}"},
    )
    assert bob_reset.status_code == 200
    assert bob_reset.json()["has_custom"] is False
    assert "bob custom prompt" not in bob_reset.json()["agent_c_instruction"]


def test_only_admin_can_update_default_prompt(auth_client: TestClient) -> None:
    admin_login = auth_client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "UnitTestAdmin123"},
    )
    assert admin_login.status_code == 200
    admin_token = admin_login.json()["access_token"]

    auth_client.post(
        "/api/v1/auth/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"username": "carol", "password": "carol-pass-123", "role": "user"},
    )

    carol_login = auth_client.post(
        "/api/v1/auth/login",
        json={"username": "carol", "password": "carol-pass-123"},
    )
    assert carol_login.status_code == 200
    carol_token = carol_login.json()["access_token"]

    denied = auth_client.put(
        "/api/v1/settings/prompt/default",
        headers={"Authorization": f"Bearer {carol_token}"},
        json={"agent_a_instruction": "not allowed"},
    )
    assert denied.status_code == 403

    updated = auth_client.put(
        "/api/v1/settings/prompt/default",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"agent_a_instruction": "admin default prompt"},
    )
    assert updated.status_code == 200
    assert "admin default prompt" in updated.json()["agent_a_instruction"]


@pytest.fixture()
def auth_client_gemini(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'assistant-gemini.db'}")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "shared-server-key")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "UnitTestAdmin123")
    app = create_app()
    return TestClient(app)


def test_llm_access_requires_personal_key_for_regular_user(auth_client_gemini: TestClient) -> None:
    admin_login = auth_client_gemini.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "UnitTestAdmin123"},
    )
    assert admin_login.status_code == 200
    admin_token = admin_login.json()["access_token"]

    created = auth_client_gemini.post(
        "/api/v1/auth/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"username": "dave", "password": "dave-pass-123", "role": "user"},
    )
    assert created.status_code == 200

    dave_login = auth_client_gemini.post(
        "/api/v1/auth/login",
        json={"username": "dave", "password": "dave-pass-123"},
    )
    assert dave_login.status_code == 200
    dave_token = dave_login.json()["access_token"]

    access = auth_client_gemini.get(
        "/api/v1/auth/llm/access",
        headers={"Authorization": f"Bearer {dave_token}"},
    )
    assert access.status_code == 200
    payload = access.json()
    assert payload["can_use_shared_key"] is False
    assert payload["requires_personal_key"] is True
    assert payload["has_personal_key"] is False


def test_set_and_clear_personal_llm_key(auth_client_gemini: TestClient) -> None:
    admin_login = auth_client_gemini.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "UnitTestAdmin123"},
    )
    admin_token = admin_login.json()["access_token"]

    auth_client_gemini.post(
        "/api/v1/auth/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"username": "erin", "password": "erin-pass-123", "role": "user"},
    )
    erin_login = auth_client_gemini.post(
        "/api/v1/auth/login",
        json={"username": "erin", "password": "erin-pass-123"},
    )
    erin_token = erin_login.json()["access_token"]

    saved = auth_client_gemini.post(
        "/api/v1/auth/llm/key",
        headers={"Authorization": f"Bearer {erin_token}"},
        json={"api_key": "unit-test-gemini-personal-key"},
    )
    assert saved.status_code == 200
    assert saved.json()["saved"] is True

    access_after_set = auth_client_gemini.get(
        "/api/v1/auth/llm/access",
        headers={"Authorization": f"Bearer {erin_token}"},
    )
    assert access_after_set.status_code == 200
    assert access_after_set.json()["has_personal_key"] is True

    cleared = auth_client_gemini.delete(
        "/api/v1/auth/llm/key",
        headers={"Authorization": f"Bearer {erin_token}"},
    )
    assert cleared.status_code == 200
    assert cleared.json()["cleared"] is True

    access_after_clear = auth_client_gemini.get(
        "/api/v1/auth/llm/access",
        headers={"Authorization": f"Bearer {erin_token}"},
    )
    assert access_after_clear.status_code == 200
    assert access_after_clear.json()["has_personal_key"] is False


def test_admin_invite_grants_shared_key_access(auth_client_gemini: TestClient) -> None:
    admin_login = auth_client_gemini.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "UnitTestAdmin123"},
    )
    admin_token = admin_login.json()["access_token"]

    auth_client_gemini.post(
        "/api/v1/auth/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"username": "frank", "password": "frank-pass-123", "role": "user"},
    )
    frank_login = auth_client_gemini.post(
        "/api/v1/auth/login",
        json={"username": "frank", "password": "frank-pass-123"},
    )
    frank_token = frank_login.json()["access_token"]

    invite = auth_client_gemini.post(
        "/api/v1/auth/shared-key-invites",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"ttl_hours": 24, "max_uses": 1, "note": "vip"},
    )
    assert invite.status_code == 200
    invite_token = invite.json()["token"]
    assert invite_token

    redeemed = auth_client_gemini.post(
        "/api/v1/auth/shared-key-invites/redeem",
        headers={"Authorization": f"Bearer {frank_token}"},
        json={"token": invite_token},
    )
    assert redeemed.status_code == 200
    assert redeemed.json()["granted"] is True

    access = auth_client_gemini.get(
        "/api/v1/auth/llm/access",
        headers={"Authorization": f"Bearer {frank_token}"},
    )
    assert access.status_code == 200
    payload = access.json()
    assert payload["can_use_shared_key"] is True
    assert payload["requires_personal_key"] is False


def test_gemini_access_and_settings_expose_display_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'assistant-gemini-metadata.db'}")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("LLM_DEFAULT_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "unit-test-gemini-shared-key")
    monkeypatch.setenv("GEMINI_FLASH_MODEL", "gemini-3.1-flash-lite")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "UnitTestAdmin123")
    monkeypatch.setattr(
        "app.llm.gemini_client.GeminiClient._discover_available_models",
        lambda self: {"gemini-2.5-flash-lite"},
    )

    app = create_app()
    client = TestClient(app)

    admin_login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "UnitTestAdmin123"},
    )
    assert admin_login.status_code == 200
    admin_token = admin_login.json()["access_token"]

    created = client.post(
        "/api/v1/auth/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"username": "metauser", "password": "metauser-pass-123", "role": "user"},
    )
    assert created.status_code == 200

    user_login = client.post(
        "/api/v1/auth/login",
        json={"username": "metauser", "password": "metauser-pass-123"},
    )
    assert user_login.status_code == 200
    user_token = user_login.json()["access_token"]

    access = client.get(
        "/api/v1/auth/llm/access",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert access.status_code == 200
    access_payload = access.json()
    assert access_payload["providers"]["gemini"].get("display_label") == "Gemini 3.1 Flash-Lite"
    assert access_payload["providers"]["gemini"].get("resolved_model") == "gemini-2.5-flash-lite"
    assert access_payload["providers"]["gemini"].get("resolution_source") == "fallback-2.5-lite"

    settings = client.get(
        "/api/v1/settings/llm",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert settings.status_code == 200
    settings_payload = settings.json()
    assert settings_payload["effective"].get("display_label") == "Gemini 3.1 Flash-Lite"
    assert settings_payload["effective"].get("resolved_model") == "gemini-2.5-flash-lite"
    assert settings_payload["effective"].get("resolution_source") == "fallback-2.5-lite"
    providers = settings_payload.get("providers", [])
    gemini_provider = next((item for item in providers if isinstance(item, dict) and item.get("id") == "gemini"), None)
    assert gemini_provider is not None
    assert isinstance(gemini_provider.get("recommended_models"), list)


def test_admin_can_manage_registration_invites_and_export(auth_client_registration_invite: TestClient) -> None:
    admin_login = auth_client_registration_invite.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "UnitTestAdmin123"},
    )
    assert admin_login.status_code == 200
    admin_token = admin_login.json()["access_token"]

    batch = auth_client_registration_invite.post(
        "/api/v1/auth/registration-invites/batch",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"count": 2, "ttl_hours": 24, "max_uses": 3, "note": "beta"},
    )
    assert batch.status_code == 200
    batch_items = batch.json()["items"]
    assert len(batch_items) == 2
    first_code = batch_items[0]["code"]
    assert first_code

    listing = auth_client_registration_invite.get(
        "/api/v1/auth/registration-invites",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert listing.status_code == 200
    listed_codes = {item["code"] for item in listing.json()["items"]}
    assert first_code in listed_codes

    export_resp = auth_client_registration_invite.get(
        "/api/v1/auth/registration-invites/export",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert export_resp.status_code == 200
    assert "text/csv" in export_resp.headers.get("content-type", "")
    assert first_code in export_resp.text

    revoked = auth_client_registration_invite.post(
        f"/api/v1/auth/registration-invites/{first_code}/revoke",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert revoked.status_code == 200
    assert revoked.json()["revoked"] is True


def test_registration_invite_exhausted_or_revoked_blocks_register(auth_client_registration_invite: TestClient) -> None:
    admin_login = auth_client_registration_invite.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "UnitTestAdmin123"},
    )
    assert admin_login.status_code == 200
    admin_token = admin_login.json()["access_token"]

    one_use = auth_client_registration_invite.post(
        "/api/v1/auth/registration-invites",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"ttl_hours": 24, "max_uses": 1, "note": "single"},
    )
    assert one_use.status_code == 200
    code = one_use.json()["code"]

    first_reg = auth_client_registration_invite.post(
        "/api/v1/auth/register",
        json={"username": "single_use_1", "password": "singleuse123", "invite_code": code},
    )
    assert first_reg.status_code == 200

    exhausted = auth_client_registration_invite.post(
        "/api/v1/auth/register",
        json={"username": "single_use_2", "password": "singleuse123", "invite_code": code},
    )
    assert exhausted.status_code == 403

    to_revoke = auth_client_registration_invite.post(
        "/api/v1/auth/registration-invites",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"ttl_hours": 24, "max_uses": 2, "note": "revoke"},
    )
    assert to_revoke.status_code == 200
    revoke_code = to_revoke.json()["code"]
    revoked = auth_client_registration_invite.post(
        f"/api/v1/auth/registration-invites/{revoke_code}/revoke",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert revoked.status_code == 200

    blocked = auth_client_registration_invite.post(
        "/api/v1/auth/register",
        json={"username": "revoked_user_1", "password": "revoked123", "invite_code": revoke_code},
    )
    assert blocked.status_code == 403
