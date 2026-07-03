from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException, Request

from app.state import AppState


def get_state(request: Request) -> AppState:
    state = getattr(request.app.state, "container", None)
    if state is None:
        raise RuntimeError("app state container not initialized")
    return state


def get_current_user(request: Request) -> dict[str, Any]:
    state = get_state(request)
    if not state.settings.auth_enabled:
        return {
            "id": "local-dev",
            "username": "local-dev",
            "role": "admin",
            "is_active": 1,
            "can_use_shared_key": 1,
            "permissions_json": json.dumps(
                {
                    "can_manage_accounts": True,
                    "can_manage_prompts": True,
                    "can_manage_shared_keys": True,
                }
            ),
        }

    auth_header = request.headers.get("Authorization", "").strip()
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = auth_header.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="missing bearer token")

    user = state.store.get_user_by_token(token)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    return user


def require_admin(request: Request) -> dict[str, Any]:
    user = get_current_user(request)
    if str(user.get("role", "")) != "admin":
        raise HTTPException(status_code=403, detail="admin role required")
    return user


def get_user_permissions(user: dict[str, Any]) -> dict[str, bool]:
    defaults = {
        "can_manage_accounts": False,
        "can_manage_prompts": False,
        "can_manage_shared_keys": False,
    }
    if str(user.get("role", "")) == "admin":
        return {
            "can_manage_accounts": True,
            "can_manage_prompts": True,
            "can_manage_shared_keys": True,
        }
    raw = user.get("permissions_json")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            for key in defaults:
                if key in parsed:
                    defaults[key] = bool(parsed[key])
    elif isinstance(raw, dict):
        for key in defaults:
            if key in raw:
                defaults[key] = bool(raw[key])
    return defaults


def require_permission(request: Request, permission_key: str) -> dict[str, Any]:
    user = get_current_user(request)
    if str(user.get("role", "")) == "admin":
        return user
    permissions = get_user_permissions(user)
    if permissions.get(permission_key):
        return user
    raise HTTPException(status_code=403, detail=f"{permission_key} permission required")
