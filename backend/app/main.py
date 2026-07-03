from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes_auth import router as auth_router
from app.api.routes_documents import router as document_router
from app.api.routes_lab import router as lab_router
from app.api.routes_settings import router as settings_router
from app.config import get_settings
from app.database import Database
from app.identity import normalize_username, validate_password, validate_username
from app.llm.provider import build_llm_client
from app.pipeline.worker import PipelineWorker
from app.services.store import Store
from app.state import AppState


def create_app() -> FastAPI:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    db = Database(settings.database_path)
    db.init_schema()
    store = Store(db)
    if settings.auth_enabled:
        admin_username = normalize_username(settings.admin_username)
        username_err = validate_username(admin_username)
        if username_err:
            raise RuntimeError(f"invalid ADMIN_USERNAME: {username_err}")
        password_err = validate_password(settings.admin_password)
        if password_err:
            raise RuntimeError(f"invalid ADMIN_PASSWORD: {password_err}")
        store.ensure_admin_account(username=admin_username, password=settings.admin_password)
    llm_client = build_llm_client(settings)
    worker = PipelineWorker(store=store, llm_client=llm_client, settings=settings)
    worker.recover_incomplete_documents()

    app = FastAPI(title="Study Assistant API", version="0.1.0")
    app.state.container = AppState(settings=settings, store=store, worker=worker)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth_router)
    app.include_router(document_router)
    app.include_router(lab_router)
    app.include_router(settings_router)

    app.mount("/assets", StaticFiles(directory=str(settings.data_dir)), name="assets")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
