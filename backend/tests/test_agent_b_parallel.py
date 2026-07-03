import threading
import time

from app.pipeline.agent_b import run_agent_b


def _build_groups_and_pages(n: int) -> tuple[list[dict], list[dict]]:
    groups: list[dict] = []
    pages: list[dict] = []
    for idx in range(1, n + 1):
        page_no = idx
        groups.append(
            {
                "id": f"g{idx}",
                "title": f"G{idx}",
                "page_start": page_no,
                "page_end": page_no,
            }
        )
        pages.append(
            {
                "page_no": page_no,
                "text_content": f"group-{idx} content key{idx}",
            }
        )
    return groups, pages


class SlowGroupLLM:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def summarize_group(self, document_summary: str, group: dict, pages: list[dict], *, instruction: str | None = None) -> dict:
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.12)
            return {
                "summary": f"summary-{group['id']}",
                "key_concepts": [group["id"]],
                "prerequisites": [],
                "misconceptions": [],
            }
        finally:
            with self._lock:
                self.active -= 1


class FlakyGroupLLM:
    def summarize_group(self, document_summary: str, group: dict, pages: list[dict], *, instruction: str | None = None) -> dict:
        if group["id"] == "g2":
            raise RuntimeError("group failure")
        return {
            "summary": f"ok-{group['id']}",
            "key_concepts": [group["id"]],
            "prerequisites": [],
            "misconceptions": [],
        }


def test_agent_b_runs_groups_in_parallel_and_keeps_order() -> None:
    llm = SlowGroupLLM()
    groups, pages = _build_groups_and_pages(6)

    out = run_agent_b(
        llm_client=llm,  # type: ignore[arg-type]
        document_summary="doc",
        groups=groups,
        pages=pages,
        instruction=None,
        concurrency=4,
    )

    assert len(out) == 6
    assert [item["id"] for item in out] == [f"g{i}" for i in range(1, 7)]
    assert llm.max_active >= 2


def test_agent_b_group_failure_falls_back_instead_of_crashing() -> None:
    llm = FlakyGroupLLM()
    groups, pages = _build_groups_and_pages(3)

    out = run_agent_b(
        llm_client=llm,  # type: ignore[arg-type]
        document_summary="doc",
        groups=groups,
        pages=pages,
        instruction=None,
        concurrency=3,
    )

    assert len(out) == 3
    item_g2 = [item for item in out if item["id"] == "g2"][0]
    assert isinstance(item_g2["summary"], str) and item_g2["summary"]
