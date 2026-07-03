from __future__ import annotations

import csv
import io
import secrets
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from app.api.deps_auth import get_current_user, get_user_permissions, require_permission
from app.emailer import send_verification_email
from app.identity import (
    auth_policy_payload,
    mask_email,
    normalize_email,
    normalize_username,
    validate_email,
    validate_password,
    validate_username,
)
from app.llm.access import can_user_use_shared_key, requires_personal_key
from app.llm.provider import (
    default_model_for_provider,
    normalize_provider,
    recommended_models_for_provider,
    resolve_provider_model_metadata,
    shared_api_key_for_provider,
)
from app.schemas import (
    AuthPolicyResponse,
    ClearPersonalLLMKeyResponse,
    CreateSharedKeyInviteRequest,
    CreateRegistrationInviteBatchRequest,
    CreateRegistrationInviteRequest,
    CreateUserRequest,
    LLMAccessResponse,
    LoginRequest,
    LoginResponse,
    RedeemSharedKeyInviteRequest,
    RedeemSharedKeyInviteResponse,
    RegistrationInviteListResponse,
    RegistrationInvitePayload,
    RegisterEmailCodeRequest,
    RegisterEmailCodeResponse,
    RegisterRequest,
    SetPersonalLLMKeyRequest,
    SetPersonalLLMKeyResponse,
    SharedKeyInviteResponse,
    UpdateUserRequest,
    UserPayload,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _generate_email_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _user_payload(user: dict[str, Any]) -> UserPayload:
    return UserPayload(
        id=str(user.get("id", "")),
        username=str(user.get("username", "")),
        email=(str(user.get("email", "")).strip() or None),
        email_verified=bool(int(user.get("email_verified", 0))),
        role=str(user.get("role", "user")),
        is_active=bool(int(user.get("is_active", 0))),
        can_use_shared_key=bool(int(user.get("can_use_shared_key", 0))),
        permissions=get_user_permissions(user),
    )


def _state(request: Request):
    container = getattr(request.app.state, "container", None)
    if container is None:
        raise RuntimeError("app state container not initialized")
    return container


def _fallback_models(state) -> dict[str, str]:
    return {
        "openai": str(state.settings.openai_model or "gpt-5.2"),
        "gemini": str(state.settings.gemini_flash_model or "gemini-3.1-flash-lite"),
        "deepseek": str(state.settings.deepseek_model or "deepseek-chat"),
        "mock": "mock",
    }


def _llm_defaults_for_user(state, user: dict[str, Any]) -> tuple[str, str, dict[str, str]]:
    user_id = str(user.get("id", "")).strip()
    global_defaults = state.store.get_global_llm_settings(
        default_provider=normalize_provider(state.settings.llm_default_provider or state.settings.llm_provider),
        default_models=_fallback_models(state),
    )
    user_defaults = state.store.get_user_llm_settings(user_id=user_id) if user_id else {}
    global_provider = normalize_provider(global_defaults.get("default_provider"), fallback=state.settings.llm_default_provider)
    global_models = global_defaults.get("default_models", {})
    if not isinstance(global_models, dict):
        global_models = _fallback_models(state)

    user_provider = normalize_provider(user_defaults.get("provider"), fallback=global_provider)
    user_model = str(user_defaults.get("model", "")).strip()
    if not user_model:
        user_model = str(global_models.get(user_provider, "")).strip() or default_model_for_provider(
            settings=state.settings,
            provider=user_provider,
        )
    return user_provider, user_model, {str(k): str(v) for k, v in global_models.items()}


def _provider_api_key_for_user(state, user: dict[str, Any], provider: str) -> str:
    normalized_provider = normalize_provider(provider, fallback=state.settings.llm_default_provider)
    if normalized_provider == "mock":
        return "mock-key"
    if can_user_use_shared_key(state, user, provider=normalized_provider):
        return str(shared_api_key_for_provider(settings=state.settings, provider=normalized_provider) or "")
    user_id = str(user.get("id", "")).strip()
    runtime_key = state.get_runtime_user_key(user_id=f"{normalized_provider}:{user_id}")
    if runtime_key:
        return runtime_key
    if not user_id:
        return ""
    return state.store.get_user_personal_llm_key(
        user_id=user_id,
        provider=normalized_provider,
        encryption_secret=state.settings.llm_key_encryption_secret,
    )


def _registration_invite_payload(*, request: Request, invite: dict[str, Any]) -> RegistrationInvitePayload:
    code = str(invite.get("code", "")).strip().upper()
    used_count = int(invite.get("used_count", 0) or 0)
    max_uses = int(invite.get("max_uses", 1) or 1)
    remaining_uses = max(0, max_uses - used_count)
    revoked_at = str(invite.get("revoked_at", "") or "").strip()
    base = str(request.base_url).rstrip("/")
    invite_url = f"{base}/?registerInvite={code}"
    return RegistrationInvitePayload(
        code=code,
        invite_url=invite_url,
        expires_at=str(invite.get("expires_at", "")),
        max_uses=max_uses,
        used_count=used_count,
        remaining_uses=remaining_uses,
        revoked=bool(revoked_at),
        note=((str(invite.get("note", "")).strip()) or None),
        created_at=str(invite.get("created_at", "")),
    )


@router.get("/policy", response_model=AuthPolicyResponse)
def auth_policy(request: Request) -> AuthPolicyResponse:
    state = _state(request)
    payload = auth_policy_payload()
    mode = str(state.settings.auth_registration_mode or "open").strip().lower()
    payload["registration"] = {
        "mode": mode,
        "invite_required": mode == "invite",
        "enabled": mode != "closed",
        "email_verification_required": bool(state.settings.auth_email_verification_required),
        "email_code_resend_seconds": int(state.settings.auth_email_code_resend_seconds),
    }
    return AuthPolicyResponse.model_validate(payload)


@router.post("/login", response_model=LoginResponse)
def login(request: Request, body: LoginRequest) -> LoginResponse:
    state = _state(request)
    username = normalize_username(body.username)
    if not username:
        raise HTTPException(status_code=400, detail="username required")
    user = state.store.verify_user_password(username=username, password=body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid username or password")
    token = state.store.create_auth_token(user_id=user["id"], ttl_hours=state.settings.auth_token_ttl_hours)
    return LoginResponse(access_token=token, user=_user_payload(user))


@router.post("/register", response_model=UserPayload)
def register(request: Request, body: RegisterRequest) -> UserPayload:
    state = _state(request)
    mode = str(state.settings.auth_registration_mode or "open").strip().lower()
    if mode == "closed":
        raise HTTPException(status_code=403, detail="registration is disabled")
    invite_code_provided = str(body.invite_code or "").strip().upper()
    if mode == "invite":
        if not invite_code_provided:
            raise HTTPException(status_code=403, detail="invite code required")

    username = normalize_username(body.username)
    username_err = validate_username(username)
    if username_err:
        raise HTTPException(status_code=400, detail=username_err)
    password_err = validate_password(body.password)
    if password_err:
        raise HTTPException(status_code=400, detail=password_err)
    if state.store.get_user_by_username(username) is not None:
        raise HTTPException(status_code=409, detail="username already exists")
    email: str | None = None
    email_verified = False
    if state.settings.auth_email_verification_required:
        email = normalize_email(body.email or "")
        email_err = validate_email(email)
        if email_err:
            raise HTTPException(status_code=400, detail=email_err)
        if state.store.get_user_by_email(email) is not None:
            raise HTTPException(status_code=409, detail="email already exists")
        code = str(body.email_code or "").strip()
        if not code:
            raise HTTPException(status_code=400, detail="email verification code required")
        if not state.store.verify_and_consume_email_code(email=email, code=code):
            raise HTTPException(status_code=403, detail="invalid or expired email verification code")
        email_verified = True
    elif body.email:
        email = normalize_email(body.email)
        email_err = validate_email(email)
        if email_err:
            raise HTTPException(status_code=400, detail=email_err)
        if state.store.get_user_by_email(email) is not None:
            raise HTTPException(status_code=409, detail="email already exists")
    user = state.store.create_user(
        username=username,
        password=body.password,
        role="user",
        email=email,
        email_verified=email_verified,
    )
    if mode == "invite":
        try:
            state.store.consume_registration_invite(
                code=invite_code_provided,
                username=username,
                email=email,
                used_by_user_id=str(user.get("id", "")),
                used_ip=((request.client.host if request.client else "") or None),
            )
        except ValueError as exc:
            # Ensure invite validation is strict: rollback just-created user if invite consumption fails.
            state.store.delete_user(user_id=str(user.get("id", "")))
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    return _user_payload(user)


@router.post("/register/email-code", response_model=RegisterEmailCodeResponse)
def send_register_email_code(request: Request, body: RegisterEmailCodeRequest) -> RegisterEmailCodeResponse:
    state = _state(request)
    if not state.settings.auth_email_verification_required:
        raise HTTPException(status_code=400, detail="email verification is disabled")
    mode = str(state.settings.auth_registration_mode or "open").strip().lower()
    if mode == "closed":
        raise HTTPException(status_code=403, detail="registration is disabled")
    email = normalize_email(body.email)
    email_err = validate_email(email)
    if email_err:
        raise HTTPException(status_code=400, detail=email_err)
    if state.store.get_user_by_email(email) is not None:
        raise HTTPException(status_code=409, detail="email already exists")

    resend_seconds = int(state.settings.auth_email_code_resend_seconds)
    latest_created_at = state.store.get_latest_email_verification_code_created_at(email=email)
    if latest_created_at is not None:
        elapsed = (datetime.now(UTC) - latest_created_at).total_seconds()
        remaining = int(max(0, resend_seconds - elapsed))
        if remaining > 0:
            raise HTTPException(
                status_code=429,
                detail=f"please wait {remaining}s before requesting another verification code",
                headers={"Retry-After": str(remaining)},
            )

    code = _generate_email_code()
    ttl_minutes = int(state.settings.auth_email_code_ttl_minutes)
    state.store.save_email_verification_code(email=email, code=code, ttl_minutes=ttl_minutes)
    try:
        send_verification_email(
            settings=state.settings,
            to_email=email,
            code=code,
            ttl_minutes=ttl_minutes,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"failed to send verification email: {exc}") from exc
    return RegisterEmailCodeResponse(
        sent=True,
        masked_email=mask_email(email),
        ttl_minutes=ttl_minutes,
        resend_after_seconds=resend_seconds,
    )


@router.get("/me", response_model=UserPayload)
def me(request: Request) -> UserPayload:
    user = get_current_user(request)
    return _user_payload(user)


@router.post("/logout")
def logout(request: Request) -> dict[str, bool]:
    state = _state(request)
    auth_header = request.headers.get("Authorization", "").strip()
    if auth_header.startswith("Bearer "):
        token = auth_header.removeprefix("Bearer ").strip()
        if token:
            state.store.delete_auth_token(token)
    return {"ok": True}


@router.get("/users", response_model=list[UserPayload])
def list_users(request: Request) -> list[UserPayload]:
    require_permission(request, "can_manage_accounts")
    state = _state(request)
    return [_user_payload(user) for user in state.store.list_users()]


@router.post("/users", response_model=UserPayload)
def create_user(request: Request, body: CreateUserRequest) -> UserPayload:
    require_permission(request, "can_manage_accounts")
    state = _state(request)
    username = normalize_username(body.username)
    username_err = validate_username(username)
    if username_err:
        raise HTTPException(status_code=400, detail=username_err)
    password_err = validate_password(body.password)
    if password_err:
        raise HTTPException(status_code=400, detail=password_err)
    role = body.role.strip().lower()
    if role not in {"admin", "user"}:
        raise HTTPException(status_code=400, detail="role must be admin or user")
    if state.store.get_user_by_username(username) is not None:
        raise HTTPException(status_code=409, detail="username already exists")
    user = state.store.create_user(
        username=username,
        password=body.password,
        role=role,
        is_active=bool(body.is_active),
        can_use_shared_key=bool(body.can_use_shared_key),
        permissions=body.permissions.model_dump(mode="python"),
    )
    return _user_payload(user)


@router.patch("/users/{user_id}", response_model=UserPayload)
def update_user(request: Request, user_id: str, body: UpdateUserRequest) -> UserPayload:
    require_permission(request, "can_manage_accounts")
    state = _state(request)
    role = body.role.strip().lower() if isinstance(body.role, str) else None
    if role is not None and role not in {"admin", "user"}:
        raise HTTPException(status_code=400, detail="role must be admin or user")
    user = state.store.update_user(
        user_id=user_id,
        role=role,
        is_active=body.is_active,
        can_use_shared_key=body.can_use_shared_key,
        permissions=body.permissions.model_dump(mode="python") if body.permissions is not None else None,
    )
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    return _user_payload(user)


@router.get("/llm/access", response_model=LLMAccessResponse)
def get_llm_access(request: Request) -> LLMAccessResponse:
    state = _state(request)
    user = get_current_user(request)
    user_id = str(user.get("id", "")).strip()
    effective_provider, effective_model, global_models = _llm_defaults_for_user(state, user)
    effective_api_key = _provider_api_key_for_user(state, user, effective_provider)
    effective_meta = resolve_provider_model_metadata(
        settings=state.settings,
        provider=effective_provider,
        model=effective_model,
        api_key=effective_api_key,
    )

    providers_payload: dict[str, dict[str, Any]] = {}
    for provider in ("openai", "gemini", "deepseek", "mock"):
        runtime_id = f"{provider}:{user_id}"
        has_personal_key = state.has_runtime_user_key(user_id=runtime_id) or state.store.has_user_personal_llm_key(
            user_id=user_id,
            provider=provider,
        )
        model = str(global_models.get(provider, "")).strip() or default_model_for_provider(
            settings=state.settings,
            provider=provider,
        )
        if provider == effective_provider:
            model = effective_model
        provider_api_key = _provider_api_key_for_user(state, user, provider)
        provider_meta = resolve_provider_model_metadata(
            settings=state.settings,
            provider=provider,
            model=model,
            api_key=provider_api_key,
        )
        providers_payload[provider] = {
            "provider": provider,
            "model": model,
            "display_label": provider_meta["display_label"],
            "resolved_model": provider_meta["resolved_model"],
            "resolution_source": provider_meta["resolution_source"],
            "has_shared_key_configured": bool(shared_api_key_for_provider(settings=state.settings, provider=provider)),
            "can_use_shared_key": can_user_use_shared_key(state, user, provider=provider),
            "requires_personal_key": requires_personal_key(state, user, provider=provider),
            "has_personal_key": bool(has_personal_key),
            "recommended_models": recommended_models_for_provider(
                settings=state.settings,
                provider=provider,
                api_key=provider_api_key,
            ),
        }

    current = providers_payload.get(effective_provider, {})
    return LLMAccessResponse(
        provider=effective_provider,
        model=effective_model,
        display_label=effective_meta["display_label"],
        resolved_model=effective_meta["resolved_model"],
        resolution_source=effective_meta["resolution_source"],
        can_use_shared_key=bool(current.get("can_use_shared_key", False)),
        requires_personal_key=bool(current.get("requires_personal_key", False)),
        has_personal_key=bool(current.get("has_personal_key", False)),
        providers=providers_payload,
    )


@router.post("/llm/key", response_model=SetPersonalLLMKeyResponse)
def set_personal_llm_key(request: Request, body: SetPersonalLLMKeyRequest) -> SetPersonalLLMKeyResponse:
    user = get_current_user(request)
    state = _state(request)
    user_id = str(user.get("id", "")).strip()
    effective_provider, _effective_model, _global_models = _llm_defaults_for_user(state, user)
    provider = normalize_provider(body.provider or effective_provider, fallback=effective_provider)
    api_key = str(body.api_key or "").strip()
    if len(api_key) < 20:
        raise HTTPException(status_code=400, detail=f"invalid {provider} api key")
    state.set_runtime_user_key(user_id=f"{provider}:{user_id}", api_key=api_key)
    state.store.upsert_user_personal_llm_key(
        user_id=user_id,
        provider=provider,
        api_key=api_key,
        encryption_secret=state.settings.llm_key_encryption_secret,
    )
    return SetPersonalLLMKeyResponse(provider=provider, saved=True, last4=api_key[-4:])


@router.delete("/llm/key", response_model=ClearPersonalLLMKeyResponse)
def clear_personal_llm_key(request: Request, provider: str | None = None) -> ClearPersonalLLMKeyResponse:
    user = get_current_user(request)
    state = _state(request)
    user_id = str(user.get("id", "")).strip()
    effective_provider, _effective_model, _global_models = _llm_defaults_for_user(state, user)
    normalized_provider = normalize_provider(provider or effective_provider, fallback=effective_provider)
    state.clear_runtime_user_key(user_id=f"{normalized_provider}:{user_id}")
    state.store.delete_user_personal_llm_key(user_id=user_id, provider=normalized_provider)
    return ClearPersonalLLMKeyResponse(provider=normalized_provider, cleared=True)


@router.post("/registration-invites", response_model=RegistrationInvitePayload)
def create_registration_invite(request: Request, body: CreateRegistrationInviteRequest) -> RegistrationInvitePayload:
    operator = require_permission(request, "can_manage_accounts")
    state = _state(request)
    invite = state.store.create_registration_invite(
        created_by_user_id=str(operator.get("id", "")),
        ttl_hours=int(body.ttl_hours),
        max_uses=int(body.max_uses),
        note=body.note,
    )
    return _registration_invite_payload(request=request, invite=invite)


@router.post("/registration-invites/batch", response_model=RegistrationInviteListResponse)
def create_registration_invites_batch(
    request: Request,
    body: CreateRegistrationInviteBatchRequest,
) -> RegistrationInviteListResponse:
    operator = require_permission(request, "can_manage_accounts")
    state = _state(request)
    invites = state.store.create_registration_invites_batch(
        created_by_user_id=str(operator.get("id", "")),
        count=int(body.count),
        ttl_hours=int(body.ttl_hours),
        max_uses=int(body.max_uses),
        note=body.note,
    )
    return RegistrationInviteListResponse(
        items=[_registration_invite_payload(request=request, invite=item) for item in invites]
    )


@router.get("/registration-invites", response_model=RegistrationInviteListResponse)
def list_registration_invites(
    request: Request,
    limit: int = 500,
) -> RegistrationInviteListResponse:
    require_permission(request, "can_manage_accounts")
    state = _state(request)
    items = state.store.list_registration_invites(limit=limit)
    return RegistrationInviteListResponse(
        items=[_registration_invite_payload(request=request, invite=item) for item in items]
    )


@router.get("/registration-invites/export")
def export_registration_invites_csv(request: Request, limit: int = 2000) -> PlainTextResponse:
    require_permission(request, "can_manage_accounts")
    state = _state(request)
    rows = state.store.list_registration_invites(limit=limit)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["code", "invite_url", "expires_at", "max_uses", "used_count", "remaining_uses", "revoked", "note", "created_at"]
    )
    for item in rows:
        payload = _registration_invite_payload(request=request, invite=item)
        writer.writerow(
            [
                payload.code,
                payload.invite_url,
                payload.expires_at,
                payload.max_uses,
                payload.used_count,
                payload.remaining_uses,
                1 if payload.revoked else 0,
                payload.note or "",
                payload.created_at,
            ]
        )
    csv_text = output.getvalue()
    return PlainTextResponse(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=registration-invites.csv"},
    )


@router.post("/registration-invites/{code}/revoke", response_model=RegistrationInvitePayload)
def revoke_registration_invite(request: Request, code: str) -> RegistrationInvitePayload:
    require_permission(request, "can_manage_accounts")
    state = _state(request)
    invite = state.store.revoke_registration_invite(code=code)
    if invite is None:
        raise HTTPException(status_code=404, detail="invite code not found")
    return _registration_invite_payload(request=request, invite=invite)


@router.post("/shared-key-invites", response_model=SharedKeyInviteResponse)
def create_shared_key_invite(request: Request, body: CreateSharedKeyInviteRequest) -> SharedKeyInviteResponse:
    operator = require_permission(request, "can_manage_shared_keys")
    state = _state(request)
    invite = state.store.create_shared_key_invite(
        created_by_user_id=str(operator.get("id", "")),
        ttl_hours=int(body.ttl_hours),
        max_uses=int(body.max_uses),
        note=body.note,
    )
    base = str(request.base_url).rstrip("/")
    invite_url = f"{base}/?sharedKeyInvite={invite['token']}"
    return SharedKeyInviteResponse(
        token=str(invite["token"]),
        invite_url=invite_url,
        expires_at=str(invite["expires_at"]),
        max_uses=int(invite["max_uses"]),
        note=invite.get("note"),
    )


@router.post("/shared-key-invites/redeem", response_model=RedeemSharedKeyInviteResponse)
def redeem_shared_key_invite(request: Request, body: RedeemSharedKeyInviteRequest) -> RedeemSharedKeyInviteResponse:
    user = get_current_user(request)
    state = _state(request)
    user_id = str(user.get("id", "")).strip()
    try:
        state.store.redeem_shared_key_invite(token=body.token, user_id=user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedeemSharedKeyInviteResponse(
        granted=True,
        can_use_shared_key=can_user_use_shared_key(state, user),
    )
