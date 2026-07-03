from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from app.llm.base import LLMClient
from app.utils.text import top_keywords


def run_agent_b(
    *,
    llm_client: LLMClient,
    document_summary: str,
    groups: list[dict],
    pages: list[dict],
    instruction: str | None = None,
    concurrency: int = 1,
) -> list[dict]:
    page_map = {page["page_no"]: page for page in pages}
    out: list[dict | None] = [None for _ in groups]

    def summarize_one(idx: int, group: dict) -> tuple[int, dict]:
        subset = [
            page_map[pno]
            for pno in range(group["page_start"], group["page_end"] + 1)
            if pno in page_map
        ]
        try:
            result = llm_client.summarize_group(document_summary, group, subset, instruction=instruction)
        except Exception:
            result = {}

        summary = str(result.get("summary", "")).strip()
        if not summary:
            raw = "\n".join(p.get("text_content", "") for p in subset)
            kws = top_keywords(raw, 4)
            summary = f"本组主要包含：{', '.join(kws)}"

        return idx, (
            {
                **group,
                "summary": summary,
                "key_concepts": [str(x) for x in result.get("key_concepts", [])][:8],
                "prerequisites": [str(x) for x in result.get("prerequisites", [])][:8],
                "misconceptions": [str(x) for x in result.get("misconceptions", [])][:8],
            }
        )

    worker_count = max(1, min(int(concurrency or 1), len(groups) or 1))
    if worker_count == 1:
        for idx, group in enumerate(groups):
            i, payload = summarize_one(idx, group)
            out[i] = payload
        return [item for item in out if isinstance(item, dict)]

    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="agent-b") as executor:
        future_map = {executor.submit(summarize_one, idx, group): idx for idx, group in enumerate(groups)}
        for future in as_completed(future_map):
            idx = future_map[future]
            try:
                i, payload = future.result()
            except Exception:
                i = idx
                group = groups[idx]
                subset = [
                    page_map[pno]
                    for pno in range(group["page_start"], group["page_end"] + 1)
                    if pno in page_map
                ]
                raw = "\n".join(p.get("text_content", "") for p in subset)
                kws = top_keywords(raw, 4)
                payload = {
                    **group,
                    "summary": f"本组主要包含：{', '.join(kws)}" if kws else "本组主要包含：核心概念",
                    "key_concepts": [str(x) for x in kws[:4]],
                    "prerequisites": [str(x) for x in kws[1:3]],
                    "misconceptions": ["注意概念边界与适用条件。"],
                }
            out[i] = payload

    return [item for item in out if isinstance(item, dict)]
