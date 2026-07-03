from pathlib import Path
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.main import create_app
from app.pipeline.formulas import looks_like_formula_candidate
from app.pipeline.preprocess import extract_pages


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'assistant.db'}")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    app = create_app()
    return TestClient(app)


def _build_pdf(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=letter)
    c.drawString(100, 720, "Chapter 1: Linear Algebra")
    c.drawString(100, 700, "Matrix equation: A x = b")
    c.drawString(100, 680, "Determinant formula: |A| = ad - bc")
    c.showPage()
    c.drawString(100, 720, "Chapter 1 continued")
    c.drawString(100, 700, "Eigen equation: A v = lambda v")
    c.showPage()
    c.save()


def _wait_for_completion(client: TestClient, document_id: str, timeout_s: float = 20.0) -> dict:
    deadline = time.time() + timeout_s
    payload = None
    while time.time() < deadline:
        resp = client.get(f"/api/v1/documents/{document_id}")
        assert resp.status_code == 200
        payload = resp.json()
        if payload["status"] in {"completed", "failed"}:
            return payload
        time.sleep(0.2)
    raise AssertionError(f"document {document_id} did not finish in time: {payload}")


def test_upload_pdf_pipeline_builds_translation_first_page_payload(client: TestClient, tmp_path: Path) -> None:
    pdf_path = tmp_path / "sample.pdf"
    _build_pdf(pdf_path)

    with pdf_path.open("rb") as f:
        upload = client.post(
            "/api/v1/documents",
            files={"file": (pdf_path.name, f, "application/pdf")},
        )
    assert upload.status_code == 200
    upload_body = upload.json()
    assert "document_id" in upload_body
    assert "job_id" in upload_body

    status = _wait_for_completion(client, upload_body["document_id"])
    assert status["status"] == "completed"
    assert status["progress"]["total_pages"] == 2

    page = client.get(f"/api/v1/documents/{upload_body['document_id']}/pages/1")
    assert page.status_code == 200
    page_body = page.json()
    assert page_body["explanation"] is None
    assert page_body["reader_mode_default"] == "translated"
    assert page_body["reading_tabs"] == ["translate"]
    assert page_body["default_tab"] == "translate"
    assert page_body["translation_overlay_status"] in {"ready", "partial"}
    assert str(page_body.get("literal_translation", "")).strip()
    assert isinstance(page_body.get("layout_blocks"), list) and len(page_body["layout_blocks"]) >= 1
    assert isinstance(page_body.get("translation_blocks"), list) and len(page_body["translation_blocks"]) >= 1
    first_block = page_body["layout_blocks"][0]
    assert set(first_block.keys()) >= {"id", "text", "bbox", "kind", "source"}
    assert set(first_block["bbox"].keys()) >= {"x", "y", "width", "height"}


def test_get_page_does_not_auto_generate_explanation_after_translation_ready(client: TestClient, tmp_path: Path) -> None:
    pdf_path = tmp_path / "no_auto_explain.pdf"
    _build_pdf(pdf_path)

    with pdf_path.open("rb") as f:
        upload = client.post("/api/v1/documents", files={"file": (pdf_path.name, f, "application/pdf")})
    assert upload.status_code == 200
    document_id = upload.json()["document_id"]
    done = _wait_for_completion(client, document_id)
    assert done["status"] == "completed"

    first = client.get(f"/api/v1/documents/{document_id}/pages/1")
    assert first.status_code == 200
    payload = first.json()
    assert payload["explanation"] is None
    assert payload["reading_tabs"] == ["translate"]
    assert payload["default_tab"] == "translate"

    second = client.get(f"/api/v1/documents/{document_id}/pages/1")
    assert second.status_code == 200
    assert second.json()["explanation"] is None


def test_explain_endpoint_generates_page_explanation_on_demand(client: TestClient, tmp_path: Path) -> None:
    pdf_path = tmp_path / "explain_on_demand.pdf"
    _build_pdf(pdf_path)

    with pdf_path.open("rb") as handle:
        upload = client.post("/api/v1/documents", files={"file": (pdf_path.name, handle, "application/pdf")})
    assert upload.status_code == 200
    document_id = upload.json()["document_id"]
    _wait_for_completion(client, document_id)

    before = client.get(f"/api/v1/documents/{document_id}/pages/1")
    assert before.status_code == 200
    assert before.json()["explanation"] is None

    explain = client.post(f"/api/v1/documents/{document_id}/pages/1/explain?language=zh")
    assert explain.status_code == 200
    explain_body = explain.json()
    assert explain_body["run_id"]
    assert explain_body["explanation"]["overview"]
    assert explain_body["explanation"]["quality"]["score"] >= 0

    after = client.get(f"/api/v1/documents/{document_id}/pages/1?language=zh")
    assert after.status_code == 200
    after_body = after.json()
    assert after_body["explanation"] is not None
    assert after_body["reading_tabs"] == ["translate", "explain"]
    assert after_body["default_tab"] == "translate"
    assert after_body["run_id"] == explain_body["run_id"]


def test_status_contains_stage_details(client: TestClient, tmp_path: Path) -> None:
    pdf_path = tmp_path / "stage.pdf"
    _build_pdf(pdf_path)

    with pdf_path.open("rb") as f:
        upload = client.post(
            "/api/v1/documents",
            files={"file": (pdf_path.name, f, "application/pdf")},
        )
    assert upload.status_code == 200
    document_id = upload.json()["document_id"]

    status = client.get(f"/api/v1/documents/{document_id}")
    assert status.status_code == 200
    payload = status.json()
    assert "stage" in payload
    assert "stage_label" in payload
    assert isinstance(payload["stage"], str) and payload["stage"]
    assert isinstance(payload["stage_label"], str) and payload["stage_label"]
    assert "pipeline_detail" in payload
    detail = payload["pipeline_detail"]
    assert isinstance(detail.get("page_status_counts"), dict)
    assert isinstance(detail.get("current_page_details"), list)
    assert isinstance(detail.get("failed_page_details"), list)
    assert isinstance(detail.get("repairable_pages"), list)
    assert isinstance(detail.get("stage_started_at"), str)
    assert isinstance(detail.get("total_started_at"), str)
    assert isinstance(detail.get("stage_elapsed_seconds"), (int, float))
    assert isinstance(detail.get("total_elapsed_seconds"), (int, float))
    assert isinstance(detail.get("c1_timeout_pages"), int)
    assert isinstance(detail.get("avg_c1_latency_ms"), (int, float))
    assert isinstance(detail.get("p95_c1_latency_ms"), (int, float))
    assert isinstance(detail.get("translation_pending"), int)
    assert isinstance(detail.get("translation_done"), int)
    assert isinstance(detail.get("translation_failed"), int)
    assert isinstance(detail.get("pro_escalation_pages"), int)
    assert isinstance(detail.get("last_resort_pages"), int)
    assert isinstance(detail.get("llm_error_counts"), dict)
    assert isinstance(detail.get("model_path_counts"), dict)
    assert isinstance(detail.get("quality_fail_streak"), int)
    assert isinstance(detail.get("adaptive_worker_reason"), str)
    assert isinstance(detail.get("coverage_lang_mode"), str)
    assert isinstance(detail.get("last_error"), str)

    final = _wait_for_completion(client, document_id)
    assert final["status"] in {"completed", "failed"}


def test_status_progress_uses_visible_done_pages(client: TestClient, tmp_path: Path) -> None:
    state = client.app.state.container
    pdf_path = tmp_path / "visible_progress.pdf"
    _build_pdf(pdf_path)

    document_id = str(uuid4())
    job_id = str(uuid4())
    state.store.create_document(
        document_id=document_id,
        original_filename=pdf_path.name,
        source_type="pdf",
        source_path=str(pdf_path),
        status="processing",
    )
    state.store.update_document_status(
        document_id,
        status="processing",
        total_pages=31,
        processed_pages=0,
        error=None,
    )
    state.store.create_job(job_id, document_id, stage="translate:blocks:23/31:w8")
    state.store.update_job(job_id, status="processing", stage="translate:blocks:23/31:w8", error=None)
    state.worker._set_pipeline_detail(
        document_id,
        stage_code="translate:blocks:23/31:w8",
        active_workers=8,
        queued_pages=8,
        done_pages=23,
        failed_pages=0,
        retry_pages=0,
        current_pages=[20, 24, 26, 27, 28, 29, 30, 31],
    )

    status = client.get(f"/api/v1/documents/{document_id}")
    assert status.status_code == 200
    payload = status.json()
    assert payload["stage"] == "translate:blocks:23/31:w8"
    assert payload["pipeline_detail"]["done_pages"] == 23
    assert payload["progress"]["processed_pages"] == 23
    assert payload["progress"]["total_pages"] == 31


def test_page_response_contains_translation_overlay_metadata(client: TestClient, tmp_path: Path) -> None:
    pdf_path = tmp_path / "view_model_density.pdf"
    _build_pdf(pdf_path)

    with pdf_path.open("rb") as f:
        upload = client.post(
            "/api/v1/documents",
            files={"file": (pdf_path.name, f, "application/pdf")},
        )
    assert upload.status_code == 200
    document_id = upload.json()["document_id"]
    done = _wait_for_completion(client, document_id)
    assert done["status"] == "completed"

    page = client.get(f"/api/v1/documents/{document_id}/pages/1")
    assert page.status_code == 200
    payload = page.json()
    assert payload.get("reader_mode_default") == "translated"
    assert isinstance(payload.get("layout_blocks"), list)
    assert isinstance(payload.get("translation_blocks"), list)
    assert isinstance(payload.get("untranslated_blocks"), list)
    assert isinstance(payload.get("literal_translation"), str)
    assert payload.get("reading_tabs") == ["translate"]
    assert payload.get("default_tab") == "translate"


def test_run_agent_c_page_uses_openai_last_resort_when_primary_fails(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = client.app.state.container
    worker = state.worker

    class OpenAILastResortStub:
        provider_name = "openai"

    calls: list[str] = []

    def fake_run_agent_c_with_quality(*, llm_client, page, global_memory, group, local_context, language, quality_threshold, instruction=None, page_budget_seconds=None):
        provider = str(getattr(llm_client, "provider_name", "unknown"))
        calls.append(provider)
        if provider != "openai":
            raise RuntimeError("Gemini request failed (status=429)")
        payload = {
            "overview": "openai last resort ok",
            "keyPoints": ["k1"],
            "conceptLinks": [],
            "formulaBlocks": [],
            "citations": [{"pageNo": 1, "span": "s", "quote": "q"}],
            "confidence": 0.8,
            "teaching": {"definition": "d", "intuition": "i", "example": "e", "focus": "f", "pitfall": "p"},
            "scaffold": {"quick30": ["a"], "understand2m": ["b"], "master5m": ["c"]},
            "continuity": {"prevBridge": "p", "thisPageNew": "n", "nextPreview": "x"},
            "microTask": {"doNow": "do", "checkQuestion": "cq", "answerHint": "ah"},
            "scopePages": [1],
            "memoryUsed": {"globalVersion": "v1", "groupId": "g1", "localPages": [1]},
        }
        quality = {"score": 90.0, "pass": True, "feedback": [], "citationScore": 100.0}
        return payload, quality, "fallback"

    monkeypatch.setattr("app.pipeline.worker.run_agent_c_with_quality", fake_run_agent_c_with_quality)
    monkeypatch.setattr(worker, "_get_openai_last_resort_client", lambda **kwargs: OpenAILastResortStub())
    monkeypatch.setattr(worker, "_raise_if_cancelled", lambda **kwargs: None)

    page = {"page_no": 1, "text_content": "alpha beta gamma", "formulas": []}
    groups = [{"id": "g1", "page_start": 1, "page_end": 1, "summary": "g"}]
    all_pages = [page]
    global_memory = {"summary": "sum", "keywords": ["alpha"], "version": 1}

    page_no, payload, quality, model_used = worker._run_agent_c_for_page(
        llm_client=worker.llm_client,
        page=page,
        groups=groups,
        all_pages=all_pages,
        global_memory=global_memory,
        language="zh",
        page_explain_instruction="",
        page_budget_seconds=30.0,
        document_id="doc-test",
        job_id="job-test",
    )

    assert calls[:2] == ["mock", "openai"]
    assert page_no == 1
    assert quality["pass"] is True
    assert model_used.startswith("openai-last-resort:")
    assert "末级兜底模型" in str(payload.get("statusHint", ""))


def test_run_agent_c_page_uses_openai_last_resort_when_quality_low(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = client.app.state.container
    worker = state.worker

    class OpenAILastResortStub:
        provider_name = "openai"

    calls: list[str] = []

    def fake_run_agent_c_with_quality(*, llm_client, page, global_memory, group, local_context, language, quality_threshold, instruction=None, page_budget_seconds=None):
        provider = str(getattr(llm_client, "provider_name", "unknown"))
        calls.append(provider)
        if provider == "openai":
            payload = {
                "overview": "openai quality recovered",
                "keyPoints": ["k1"],
                "conceptLinks": [],
                "formulaBlocks": [],
                "citations": [{"pageNo": 1, "span": "s", "quote": "q"}],
                "confidence": 0.8,
                "teaching": {"definition": "d", "intuition": "i", "example": "e", "focus": "f", "pitfall": "p"},
                "scaffold": {"quick30": ["a"], "understand2m": ["b"], "master5m": ["c"]},
                "continuity": {"prevBridge": "p", "thisPageNew": "n", "nextPreview": "x"},
                "microTask": {"doNow": "do", "checkQuestion": "cq", "answerHint": "ah"},
                "scopePages": [1],
                "memoryUsed": {"globalVersion": "v1", "groupId": "g1", "localPages": [1]},
            }
            quality = {"score": 88.0, "pass": True, "feedback": [], "citationScore": 100.0}
            return payload, quality, "fallback"
        payload = {
            "overview": "primary low quality",
            "keyPoints": ["k1"],
            "conceptLinks": [],
            "formulaBlocks": [],
            "citations": [],
            "confidence": 0.55,
            "teaching": {"definition": "d", "intuition": "i", "example": "", "focus": "f", "pitfall": "p"},
            "scaffold": {"quick30": ["a"], "understand2m": ["b"], "master5m": ["c"]},
            "continuity": {"prevBridge": "p", "thisPageNew": "n", "nextPreview": "x"},
            "microTask": {"doNow": "do", "checkQuestion": "cq", "answerHint": "ah"},
            "scopePages": [1],
            "memoryUsed": {"globalVersion": "v1", "groupId": "g1", "localPages": [1]},
        }
        quality = {"score": 50.0, "pass": False, "feedback": ["coverage low"], "citationScore": 0.0}
        return payload, quality, "flash"

    monkeypatch.setattr("app.pipeline.worker.run_agent_c_with_quality", fake_run_agent_c_with_quality)
    monkeypatch.setattr(worker, "_get_openai_last_resort_client", lambda **kwargs: OpenAILastResortStub())
    monkeypatch.setattr(worker, "_raise_if_cancelled", lambda **kwargs: None)

    page = {"page_no": 1, "text_content": "alpha beta gamma", "formulas": []}
    groups = [{"id": "g1", "page_start": 1, "page_end": 1, "summary": "g"}]
    all_pages = [page]
    global_memory = {"summary": "sum", "keywords": ["alpha"], "version": 1}

    page_no, payload, quality, model_used = worker._run_agent_c_for_page(
        llm_client=worker.llm_client,
        page=page,
        groups=groups,
        all_pages=all_pages,
        global_memory=global_memory,
        language="zh",
        page_explain_instruction="",
        page_budget_seconds=30.0,
        document_id="doc-test",
        job_id="job-test",
    )

    assert calls[:2] == ["mock", "openai"]
    assert page_no == 1
    assert quality["pass"] is True
    assert model_used.startswith("openai-last-resort:")
    assert payload["overview"] == "openai quality recovered"


def test_extract_pages_keeps_text_formulas_and_images(client: TestClient, tmp_path: Path) -> None:
    pdf_path = tmp_path / "formulas.pdf"
    _build_pdf(pdf_path)
    output_dir = tmp_path / "preprocess"

    pages = extract_pages(pdf_path, output_dir, client.app.state.container.worker.llm_client)

    assert len(pages) == 2
    first_page = pages[0]
    assert first_page["text_content"]
    assert first_page["image_path"]
    assert Path(first_page["image_path"]).exists()
    assert isinstance(first_page["embedding"], list)
    assert any(looks_like_formula_candidate(item["latex"]) for item in first_page["formulas"])
