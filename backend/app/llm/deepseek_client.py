from __future__ import annotations

import json
from typing import Any

import httpx

from app.llm.openai_client import OpenAIClient


class DeepSeekClient(OpenAIClient):
    provider_name = "deepseek"
    _DEFAULT_HTTP_TIMEOUT_S = 60.0

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "deepseek-chat",
        base_url: str = "https://api.deepseek.com",
    ) -> None:
        super().__init__(
            api_key=api_key,
            model=model or "deepseek-chat",
            base_url=base_url or "https://api.deepseek.com",
        )

    def _request_json(
        self,
        *,
        prompt: str,
        schema: dict[str, Any] | None = None,
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        schema_hint = ""
        if schema is not None:
            schema_hint = (
                "\n\n请严格输出一个 JSON object，并尽量满足这个结构提示：\n"
                f"{json.dumps(schema, ensure_ascii=False)[:6000]}"
            )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是学习助手的结构化输出模型。只输出 JSON object，不要输出 Markdown。",
                },
                {"role": "user", "content": f"{prompt}{schema_hint}"},
            ],
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }

        try:
            with httpx.Client(timeout=max(8.0, float(self._DEFAULT_HTTP_TIMEOUT_S))) as client:
                response = client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            status = int(exc.response.status_code) if exc.response is not None else 0
            body = ""
            if exc.response is not None:
                try:
                    body = exc.response.text
                except Exception:
                    body = ""
            raise RuntimeError(
                f"DeepSeek chat completions request failed (status={status}, model={self.model}): {body[:500]}"
            ) from exc
        except Exception as exc:
            raise RuntimeError(f"DeepSeek chat completions request failed (model={self.model}): {exc}") from exc

        choices = data.get("choices", []) if isinstance(data, dict) else []
        if not choices:
            raise RuntimeError("DeepSeek response did not include choices")
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        text = str(message.get("content", "")).strip()
        parsed = self._extract_json(text)
        if not isinstance(parsed, dict):
            raise RuntimeError("DeepSeek structured output is not a JSON object")
        return parsed
