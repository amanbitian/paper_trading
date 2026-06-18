"""
Ollama local model client.
GPU: RTX 4080 (16 GB VRAM)
Recommended models (pull with `ollama pull <model>`):
  - qwen3:14b   (~9 GB VRAM) - default, best balance of speed/quality
  - qwen3:8b    (~5 GB VRAM) - fast fallback
  - qwen3:32b:q4_K_M (~10 GB VRAM) - higher quality, slower
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DISCLAIMER = (
    "Educational analysis only. Not financial advice, not a trade recommendation, "
    "and not a future price prediction."
)


class OllamaUnavailableError(Exception):
    pass


class OllamaJSONError(Exception):
    pass


@dataclass(frozen=True)
class OllamaSettings:
    base_url: str
    default_model: str
    fallback_model: str
    timeout_seconds: int
    max_tokens: int


class OllamaClient:
    def __init__(self, settings: OllamaSettings) -> None:
        self.settings = settings
        self._base = settings.base_url.rstrip("/")
        self.last_chat_log: dict[str, Any] | None = None

    def cache_key(self, prompt: str, model: str) -> str:
        digest = hashlib.sha256(f"{model}:{prompt}".encode("utf-8")).hexdigest()
        return digest

    async def is_available(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self._base}/api/tags")
                if response.status_code != 200:
                    logger.info(
                        "ollama_ping base_url=%s connected=false status_code=%s",
                        self._base,
                        response.status_code,
                    )
                    return False
                models = response.json().get("models") or []
                connected = len(models) > 0
                logger.info(
                    "ollama_ping base_url=%s connected=%s models_loaded=%s",
                    self._base,
                    connected,
                    len(models),
                )
                return connected
        except Exception as exc:
            logger.warning("ollama_ping base_url=%s connected=false error=%s", self._base, exc)
            return False

    async def list_models(self) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self._base}/api/tags")
                response.raise_for_status()
                return [
                    item.get("name", "")
                    for item in response.json().get("models", [])
                    if item.get("name")
                ]
        except Exception:
            return []

    async def chat(
        self,
        prompt: str,
        system: str = "",
        model: str | None = None,
        expect_json: bool = False,
        timeout: int | None = None,
    ) -> str:
        primary = model or self.settings.default_model
        timeout_sec = timeout or self.settings.timeout_seconds
        try:
            return await self._chat_once(
                prompt=prompt,
                system=system,
                model=primary,
                timeout=timeout_sec,
                expect_json=expect_json,
            )
        except (OllamaUnavailableError, httpx.TimeoutException) as exc:
            if primary == self.settings.fallback_model:
                raise OllamaUnavailableError(str(exc)) from exc
            logger.warning("Ollama primary model failed (%s); retrying with %s", primary, self.settings.fallback_model)
            return await self._chat_once(
                prompt=prompt,
                system=system,
                model=self.settings.fallback_model,
                timeout=timeout_sec,
                expect_json=expect_json,
            )

    async def _chat_once(
        self,
        *,
        prompt: str,
        system: str,
        model: str,
        timeout: int,
        expect_json: bool,
    ) -> str:
        payload: dict[str, Any] = {
            "model": model,
            "stream": False,
            "messages": [],
            "options": {"num_predict": self.settings.max_tokens},
        }
        if system:
            payload["messages"].append({"role": "system", "content": system})
        payload["messages"].append({"role": "user", "content": prompt})

        preview = prompt.replace("\n", " ")[:700]
        logger.info(
            "ollama_chat_start base_url=%s model=%s timeout_s=%s prompt_chars=%s query_preview=%s",
            self._base,
            model,
            timeout,
            len(prompt),
            preview,
        )

        try:
            async with httpx.AsyncClient(timeout=float(timeout)) as client:
                response = await client.post(f"{self._base}/api/chat", json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.ConnectError as exc:
            raise OllamaUnavailableError("Cannot connect to Ollama. Is `ollama serve` running?") from exc
        except httpx.TimeoutException as exc:
            raise OllamaUnavailableError("Ollama request timed out.") from exc
        except httpx.HTTPError as exc:
            raise OllamaUnavailableError(f"Ollama HTTP error: {exc}") from exc

        text = (data.get("message") or {}).get("content", "").strip()
        if not text:
            raise OllamaUnavailableError("Ollama returned an empty response.")

        if expect_json:
            self._validate_json(text)

        self.last_chat_log = {
            "model": model,
            "base_url": self._base,
            "system": system,
            "prompt": prompt,
            "response": text,
        }
        logger.info(
            "ollama_chat_done model=%s response_chars=%s response_preview=%s",
            model,
            len(text),
            text.replace("\n", " ")[:700],
        )
        return text

    @staticmethod
    def _validate_json(text: str) -> None:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            cleaned = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        try:
            json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise OllamaJSONError(f"Response is not valid JSON: {exc}") from exc

    @staticmethod
    def parse_json_response(text: str) -> dict[str, Any]:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            cleaned = "\n".join(lines[1:-1] if lines and lines[-1].strip() == "```" else lines[1:])
        parsed = json.loads(cleaned)
        if not isinstance(parsed, dict):
            raise OllamaJSONError("Expected a JSON object.")
        return parsed
