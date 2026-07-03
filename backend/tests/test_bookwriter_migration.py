from pathlib import Path
import time

import pytest
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.main import create_app


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'assistant.db'}")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    app = create_app()
    return TestClient(app)


def _build_pdf(path: Path, *, pages: int = 2) -> None:
    c = canvas.Canvas(str(path), pagesize=letter)
    for idx in range(1, pages + 1):
        c.drawString(72, 720, f"BookWriter migration page {idx}")
        c.drawString(72, 698, f"Matrix equation page {idx}: A x = b")
        c.drawString(72, 676, f"Detail page {idx}: explain the formula carefully")
        c.showPage()
    c.save()


def _wait_for_completion(client: TestClient, document_id: str, timeout_s: float = 20.0) -> dict:
    deadline = time.time() + timeout_s
    latest = None
    while time.time() < deadline:
        resp = client.get(f"/api/v1/documents/{document_id}")
        assert resp.status_code == 200
        latest = resp.json()
        if latest["status"] in {"completed", "failed", "canceled"}:
            return latest
        time.sleep(0.2)
    raise AssertionError(f"document did not finish in time: {latest}")


def test_document_list_supports_active_and_library_views(client: TestClient) -> None:
    state = client.app.state.container
    state.store.create_document(
        document_id="doc-library",
        original_filename="library.pdf",
        source_type="pdf",
        source_path="/tmp/library.pdf",
        status="completed",
    )
    state.store.update_document_status("doc-library", status="completed", total_pages=4, processed_pages=4)
    state.store.create_job("job-library", "doc-library", stage="completed")
    state.store.update_job("job-library", status="completed", stage="completed")

    state.store.create_document(
        document_id="doc-active",
        original_filename="active.pdf",
        source_type="pdf",
        source_path="/tmp/active.pdf",
        status="processing",
    )
    state.store.update_document_status("doc-active", status="processing", total_pages=5, processed_pages=1)
    state.store.create_job("job-active", "doc-active", stage="agent_c1:draft:1/5:w2")
    state.store.update_job("job-active", status="processing", stage="agent_c1:draft:1/5:w2")

    active = client.get("/api/v1/documents?view=active&limit=20")
    assert active.status_code == 200
    active_items = active.json()["items"]
    assert [item["document_id"] for item in active_items] == ["doc-active"]
    assert active_items[0]["has_active_job"] is True
    assert "last_page_no" in active_items[0]
    assert "latest_run_id" in active_items[0]

    library = client.get("/api/v1/documents?view=library&limit=20")
    assert library.status_code == 200
    library_items = library.json()["items"]
    assert [item["document_id"] for item in library_items] == ["doc-library"]
    assert library_items[0]["has_active_job"] is False

    all_docs = client.get("/api/v1/documents?view=all&limit=20")
    assert all_docs.status_code == 200
    assert {item["document_id"] for item in all_docs.json()["items"]} == {"doc-library", "doc-active"}


def test_upload_creates_run_snapshot_with_learning_profile(client: TestClient, tmp_path: Path) -> None:
    pdf_path = tmp_path / "learning-profile.pdf"
    _build_pdf(pdf_path, pages=2)

    with pdf_path.open("rb") as handle:
        upload = client.post(
            "/api/v1/documents",
            files={"file": (pdf_path.name, handle, "application/pdf")},
            data={
                "learner_level": "beginner",
                "learning_goal": "exam",
                "depth_mode": "deep",
                "attention_support": "adhd_friendly",
            },
        )
    assert upload.status_code == 200
    document_id = upload.json()["document_id"]
    _wait_for_completion(client, document_id)

    runs_resp = client.get(f"/api/v1/documents/{document_id}/runs")
    assert runs_resp.status_code == 200
    runs = runs_resp.json()["items"]
    assert len(runs) >= 1
    run_id = runs[0]["run_id"]
    assert runs[0]["trigger_type"] == "upload"
    assert runs[0]["scope_type"] == "document"
    assert runs[0]["learning_profile"]["learner_level"] == "beginner"
    assert runs[0]["learning_profile"]["learning_goal"] == "exam"
    assert runs[0]["learning_profile"]["depth_mode"] == "deep"
    assert runs[0]["learning_profile"]["attention_support"] == "adhd_friendly"

    detail_resp = client.get(f"/api/v1/documents/{document_id}/runs/{run_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["run_id"] == run_id
    assert detail["prompt_snapshot"]["agent_c_instruction"]
    assert detail["model_chain"]
    assert detail["quality_stats"]

    run_meta = tmp_path / "data" / "documents" / document_id / "runs" / run_id / "run_meta.json"
    assert run_meta.exists()


def test_regenerate_page_returns_run_id_and_page_run_status(client: TestClient, tmp_path: Path) -> None:
    pdf_path = tmp_path / "regenerate.pdf"
    _build_pdf(pdf_path, pages=2)

    with pdf_path.open("rb") as handle:
        upload = client.post("/api/v1/documents", files={"file": (pdf_path.name, handle, "application/pdf")})
    assert upload.status_code == 200
    document_id = upload.json()["document_id"]
    _wait_for_completion(client, document_id)

    regen = client.post(
        f"/api/v1/documents/{document_id}/pages/1/regenerate?language=zh&depth_mode=quick&attention_support=standard"
    )
    assert regen.status_code == 200
    regen_body = regen.json()
    assert regen_body["run_id"]

    page_resp = client.get(f"/api/v1/documents/{document_id}/pages/1?language=zh")
    assert page_resp.status_code == 200
    page_body = page_resp.json()
    assert page_body["run_id"] == regen_body["run_id"]
    assert page_body["latest_run_status"] in {"processing", "completed", "failed"}
    assert page_body["chapter_nav"]["groups"]
    assert page_body["evidence_drawer"]["scope_pages"]


def test_explanation_preview_is_ephemeral(client: TestClient) -> None:
    before = client.get("/api/v1/documents?view=all&limit=50")
    assert before.status_code == 200
    before_count = len(before.json()["items"])

    preview = client.post(
        "/api/v1/lab/explanation-preview",
        json={
            "page_text": "Factorial design estimates interaction effects instead of changing one factor at a time.",
            "formulas": [{"latex": "y = \\mu + \\alpha_i + \\beta_j + (\\alpha\\beta)_{ij} + \\varepsilon_{ij}"}],
            "language": "zh",
            "learner_level": "beginner",
            "learning_goal": "understand",
            "depth_mode": "standard",
            "attention_support": "adhd_friendly",
            "prompt_overrides": {
                "agent_c_instruction": "先说这页到底在干什么，再分三步讲清楚。"
            },
        },
    )
    assert preview.status_code == 200
    payload = preview.json()
    assert payload["explanation_preview"]["overview"]
    assert payload["translation_preview"]
    assert payload["quality_preview"]["score"] >= 0
    assert payload["model_meta"]["provider"]
    assert payload["model_meta"]["display_label"]

    after = client.get("/api/v1/documents?view=all&limit=50")
    assert after.status_code == 200
    after_count = len(after.json()["items"])
    assert after_count == before_count
