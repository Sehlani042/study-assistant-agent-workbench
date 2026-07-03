from pathlib import Path
import time

from fastapi.testclient import TestClient
import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.main import create_app
from app.pipeline.agent_c import _build_scope_pages
from app.pipeline.retrieval import select_local_context
from app.services.quality import evaluate_page_explanation


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'assistant.db'}")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    app = create_app()
    return TestClient(app)


def _build_pdf(path: Path, pages: int = 6) -> None:
    c = canvas.Canvas(str(path), pagesize=letter)
    for i in range(1, pages + 1):
        c.drawString(100, 720, f"Page {i} title")
        c.drawString(100, 700, f"Page {i} discusses continuity and step-by-step learning.")
        c.showPage()
    c.save()


def test_scope_pages_only_report_actual_context_pages() -> None:
    assert _build_scope_pages(2, [1, 3]) == [1, 2, 3]


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


def test_document_status_includes_pipeline_detail(client: TestClient, tmp_path: Path) -> None:
    pdf_path = tmp_path / "pipeline_detail.pdf"
    _build_pdf(pdf_path, pages=4)

    with pdf_path.open("rb") as f:
        upload = client.post("/api/v1/documents", files={"file": (pdf_path.name, f, "application/pdf")})
    assert upload.status_code == 200
    document_id = upload.json()["document_id"]

    status = client.get(f"/api/v1/documents/{document_id}")
    assert status.status_code == 200
    payload = status.json()
    assert "pipeline_detail" in payload
    detail = payload["pipeline_detail"]
    assert set(detail.keys()) >= {
        "stage_code",
        "running_agent",
        "active_workers",
        "queued_pages",
        "done_pages",
        "failed_pages",
        "retry_pages",
        "current_pages",
        "page_status_counts",
        "current_page_details",
        "failed_page_details",
        "repairable_pages",
        "stage_started_at",
        "total_started_at",
        "stage_elapsed_seconds",
        "total_elapsed_seconds",
        "c1_timeout_pages",
        "avg_c1_latency_ms",
        "p95_c1_latency_ms",
        "translation_pending",
        "translation_done",
        "translation_failed",
        "last_error",
    }

    _wait_for_completion(client, document_id)


def test_outline_includes_learning_arc(client: TestClient, tmp_path: Path) -> None:
    pdf_path = tmp_path / "learning_arc.pdf"
    _build_pdf(pdf_path, pages=4)
    with pdf_path.open("rb") as f:
        upload = client.post("/api/v1/documents", files={"file": (pdf_path.name, f, "application/pdf")})
    assert upload.status_code == 200
    document_id = upload.json()["document_id"]
    done = _wait_for_completion(client, document_id)
    assert done["status"] == "completed"

    outline = client.get(f"/api/v1/documents/{document_id}/outline")
    assert outline.status_code == 200
    payload = outline.json()
    assert "learning_arc" in payload
    assert isinstance(payload["learning_arc"], list)


def test_page_explanation_contains_scaffold_continuity_and_microtask(client: TestClient, tmp_path: Path) -> None:
    pdf_path = tmp_path / "scaffold.pdf"
    _build_pdf(pdf_path, pages=4)
    with pdf_path.open("rb") as f:
        upload = client.post("/api/v1/documents", files={"file": (pdf_path.name, f, "application/pdf")})
    assert upload.status_code == 200
    document_id = upload.json()["document_id"]
    done = _wait_for_completion(client, document_id)
    assert done["status"] == "completed"

    explain = client.post(f"/api/v1/documents/{document_id}/pages/2/explain?language=zh")
    assert explain.status_code == 200

    page = client.get(f"/api/v1/documents/{document_id}/pages/2")
    assert page.status_code == 200
    explanation = page.json()["explanation"]
    assert isinstance(explanation.get("scaffold"), dict)
    assert set(explanation["scaffold"].keys()) >= {"quick30", "understand2m", "master5m"}
    assert isinstance(explanation.get("continuity"), dict)
    assert set(explanation["continuity"].keys()) >= {"prevBridge", "thisPageNew", "nextPreview"}
    assert isinstance(explanation.get("microTask"), dict)
    assert set(explanation["microTask"].keys()) >= {"doNow", "checkQuestion", "answerHint"}
    assert isinstance(explanation.get("scopePages"), list)
    assert 2 in explanation["scopePages"]
    assert isinstance(explanation.get("literalTranslation"), str)
    assert explanation.get("literalTranslation")
    assert explanation.get("translationStatus") in {"pending", "ready", "failed"}
    assert isinstance(explanation.get("clarity"), dict)
    assert set(explanation["clarity"].keys()) >= {"conclusion", "steps", "example"}
    assert isinstance(explanation.get("evidenceBlocks"), list)
    if explanation["evidenceBlocks"]:
        first = explanation["evidenceBlocks"][0]
        assert set(first.keys()) >= {"kind", "claim", "citations"}


def test_page_response_contains_status_hint(client: TestClient, tmp_path: Path) -> None:
    pdf_path = tmp_path / "status_hint.pdf"
    _build_pdf(pdf_path, pages=2)
    with pdf_path.open("rb") as f:
        upload = client.post("/api/v1/documents", files={"file": (pdf_path.name, f, "application/pdf")})
    assert upload.status_code == 200
    document_id = upload.json()["document_id"]
    done = _wait_for_completion(client, document_id)
    assert done["status"] == "completed"

    page = client.get(f"/api/v1/documents/{document_id}/pages/1")
    assert page.status_code == 200
    payload = page.json()
    assert isinstance(payload.get("statusHint"), str)
    assert payload["statusHint"]


def test_chat_response_includes_scope_pages(client: TestClient, tmp_path: Path) -> None:
    pdf_path = tmp_path / "chat_scope_pages.pdf"
    _build_pdf(pdf_path, pages=4)
    with pdf_path.open("rb") as f:
        upload = client.post("/api/v1/documents", files={"file": (pdf_path.name, f, "application/pdf")})
    assert upload.status_code == 200
    document_id = upload.json()["document_id"]
    done = _wait_for_completion(client, document_id)
    assert done["status"] == "completed"

    chat = client.post(
        f"/api/v1/documents/{document_id}/pages/2/chat",
        json={"question": "这一页先看什么？", "language": "zh"},
    )
    assert chat.status_code == 200
    payload = chat.json()["answer"]
    assert "scopePages" in payload
    assert isinstance(payload["scopePages"], list)
    assert 2 in payload["scopePages"]


def test_select_local_context_includes_cross_group_neighbors() -> None:
    pages = []
    for i in range(1, 8):
        pages.append(
            {
                "page_no": i,
                "group_id": "g1" if i <= 4 else "g2",
                "text_content": f"content {i}",
                "embedding": [1.0 if i % 2 == 0 else 0.8, 0.5],
            }
        )
    current = pages[3]  # page 4, boundary between g1 and g2
    selected = select_local_context(current_page=current, all_pages=pages, top_k=2)
    selected_pages = [item["page_no"] for item in selected]
    assert 2 in selected_pages
    assert 3 in selected_pages
    assert 5 in selected_pages
    assert 6 in selected_pages


def test_quality_gate_hard_fails_without_required_continuity_fields() -> None:
    explanation = {
        "overview": "本页讲方程 Ax=b 的意义。",
        "keyPoints": ["Ax=b 是线性系统表达。"],
        "conceptLinks": [],
        "formulaBlocks": [],
        "citations": [{"pageNo": 1, "span": "line", "quote": "Ax=b"}],
        "confidence": 0.8,
        "teaching": {
            "definition": "定义",
            "intuition": "直觉",
            "example": "",
            "focus": "重点",
            "pitfall": "易错点",
        },
        "memoryUsed": {"globalVersion": "v1", "groupId": "g1", "localPages": [1, 2]},
        "scaffold": {"quick30": ["先看 Ax=b"], "understand2m": [], "master5m": []},
        "continuity": {"prevBridge": "", "thisPageNew": "引入矩阵方程", "nextPreview": ""},
        "microTask": {"doNow": "", "checkQuestion": "", "answerHint": ""},
        "scopePages": [1, 2],
    }
    quality = evaluate_page_explanation(
        page_no=1,
        page_text="Ax=b",
        explanation=explanation,
        global_keywords=["matrix", "equation"],
        threshold=80,
    )
    assert quality["pass"] is False
    assert any("连续" in item for item in quality["feedback"])


def test_quality_gate_rejects_template_like_explanations() -> None:
    explanation = {
        "overview": "Why Use Factorial Designs",
        "keyPoints": ["one-at-a-time", "interaction", "factorial designs"],
        "conceptLinks": [],
        "formulaBlocks": [],
        "citations": [{"pageNo": 3, "span": "line", "quote": "interaction"}],
        "confidence": 0.8,
        "teaching": {
            "definition": "Why Use Factorial Designs",
            "intuition": "直觉上可把它理解为：one-at-a-time designs.",
            "example": "可结合本页关键词进行练习：one,at,time",
            "focus": "",
            "pitfall": "不要只记结论，忽略公式适用条件。",
        },
        "memoryUsed": {"globalVersion": "v1", "groupId": "g1", "localPages": [2, 3, 4]},
        "scaffold": {
            "quick30": ["Why Use Factorial Designs"],
            "understand2m": ["Alternative: One-at-a-time"],
            "master5m": ["尝试复述本页重点并给一个例子。"],
        },
        "continuity": {
            "prevBridge": "承接上一页的核心结论。",
            "thisPageNew": "Why Use Factorial Designs",
            "nextPreview": "下一页会继续深化本页概念。",
        },
        "microTask": {
            "doNow": "用一句话复述本页。",
            "checkQuestion": "如果把术语换成白话，你还能解释吗？",
            "answerHint": "答案提示：回到定义。",
        },
        "scopePages": [1, 2, 3, 4, 5],
    }
    quality = evaluate_page_explanation(
        page_no=3,
        page_text="Why Use Factorial Designs one-at-a-time interaction",
        explanation=explanation,
        global_keywords=["factorial", "interaction"],
        threshold=80,
        language="zh",
    )
    assert quality["pass"] is False
    assert any("模板" in item for item in quality["feedback"])


def test_quality_gate_rejects_non_chinese_output_in_zh_mode() -> None:
    explanation = {
        "overview": "This page introduces matrix equation Ax=b.",
        "keyPoints": ["Matrix form", "Unknown vector", "Solve the system"],
        "conceptLinks": [],
        "formulaBlocks": [],
        "citations": [{"pageNo": 1, "span": "line", "quote": "Ax=b"}],
        "confidence": 0.8,
        "teaching": {
            "definition": "Ax=b is a compact representation for a linear system.",
            "intuition": "Think of A as a machine that maps x to b.",
            "example": "Given A and b, we find x.",
            "focus": "Interpret each symbol correctly.",
            "pitfall": "Do not treat A and x as scalars.",
        },
        "memoryUsed": {"globalVersion": "v1", "groupId": "g1", "localPages": [1, 2]},
        "scaffold": {
            "quick30": ["What this page does"],
            "understand2m": ["Why Ax=b matters"],
            "master5m": ["Try one concrete system"],
        },
        "continuity": {
            "prevBridge": "承接上一页：复习线性方程组。",
            "thisPageNew": "本页新增：矩阵形式 Ax=b。",
            "nextPreview": "下一页预告：求解方法。",
        },
        "microTask": {
            "doNow": "写下 A, x, b 分别代表什么。",
            "checkQuestion": "为什么要写成 Ax=b？",
            "answerHint": "为了统一表示并便于算法处理。",
        },
        "scopePages": [1, 2, 3],
    }
    quality = evaluate_page_explanation(
        page_no=1,
        page_text="Ax=b",
        explanation=explanation,
        global_keywords=["matrix", "equation"],
        threshold=80,
        language="zh",
    )
    assert quality["pass"] is False
    assert any("中文" in item for item in quality["feedback"])


def test_quality_coverage_uses_crosslingual_mode_for_zh_explanation() -> None:
    explanation = {
        "overview": "本页讲的是 factorial design 的 interaction 估计思路。",
        "keyPoints": ["关注 interaction term 的解释", "对比 one-at-a-time 的限制"],
        "conceptLinks": ["factorial design", "interaction"],
        "formulaBlocks": [],
        "citations": [{"pageNo": 1, "span": "line", "quote": "interaction"}],
        "confidence": 0.8,
        "teaching": {
            "definition": "析因设计可以估计交互作用。",
            "intuition": "多个因素一起变动时，效应可能不是简单相加。",
            "example": "温度和压力的组合会让产率出现非线性变化。",
            "focus": "先看 interaction 的定义，再看估计方式。",
            "pitfall": "不要把交互项当作主效应相加。",
        },
        "memoryUsed": {"globalVersion": "v1", "groupId": "g1", "localPages": [1, 2]},
        "scaffold": {
            "quick30": ["这页核心：interaction 比主效应更关键。"],
            "understand2m": ["先识别 interaction term 再解释其物理意义。"],
            "master5m": ["尝试写一个 2 因素交互项并解释。"],
        },
        "continuity": {
            "prevBridge": "上一页刚讲主效应。",
            "thisPageNew": "本页新增交互作用估计。",
            "nextPreview": "下一页将进入方差分解。",
        },
        "microTask": {
            "doNow": "找出本页出现的 interaction 术语。",
            "checkQuestion": "为什么 one-at-a-time 估不准 interaction？",
            "answerHint": "因为它固定了其他因素，无法观察联合变化。",
        },
        "scopePages": [1, 2, 3],
    }
    quality = evaluate_page_explanation(
        page_no=1,
        page_text="Why Use Factorial Designs interaction one-at-a-time",
        explanation=explanation,
        global_keywords=["factorial", "interaction"],
        threshold=80,
        language="zh",
    )
    assert quality["coverageLangMode"] in {"dual_zh_crosslingual", "dual_default"}
    assert quality["coverage"] >= 0


def test_quality_gate_rejects_high_semantic_overlap() -> None:
    repeated = "本页核心是理解 Ax=b 的矩阵表示，并据此求解线性系统。"
    explanation = {
        "overview": repeated,
        "keyPoints": [repeated, repeated, repeated],
        "conceptLinks": [],
        "formulaBlocks": [],
        "citations": [{"pageNo": 1, "span": "line", "quote": "Ax=b"}],
        "confidence": 0.8,
        "teaching": {
            "definition": repeated,
            "intuition": repeated,
            "example": repeated,
            "focus": repeated,
            "pitfall": repeated,
        },
        "memoryUsed": {"globalVersion": "v1", "groupId": "g1", "localPages": [1, 2]},
        "scaffold": {
            "quick30": [repeated],
            "understand2m": [repeated],
            "master5m": [repeated],
        },
        "continuity": {
            "prevBridge": "承接上一页：复习线性方程组。",
            "thisPageNew": repeated,
            "nextPreview": "下一页预告：用消元法求解。",
        },
        "microTask": {
            "doNow": repeated,
            "checkQuestion": repeated,
            "answerHint": repeated,
        },
        "clarity": {
            "conclusion": repeated,
            "steps": [repeated, repeated, repeated],
            "example": repeated,
        },
        "scopePages": [1, 2, 3],
    }
    quality = evaluate_page_explanation(
        page_no=1,
        page_text="Ax=b",
        explanation=explanation,
        global_keywords=["matrix", "equation"],
        threshold=80,
        language="zh",
    )
    assert "semanticOverlapScore" in quality
    assert quality["pass"] is False
    assert any(("重复" in item) or ("冗余" in item) for item in quality["feedback"])
