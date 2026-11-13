"""Minimal Ollama chat/completion client.

Only transport lives here; the agents own their prompts. If Ollama is not
running, callers get ``LLMUnavailableError`` and fall back to the bundled
rule-based generator so the app still works offline.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from app.config import Settings, get_settings


class LLMUnavailableError(RuntimeError):
    """Raised when the configured LLM backend cannot be reached."""


@dataclass(slots=True)
class Completion:
    text: str
    model: str
    provider: str
    latency_ms: int


class OllamaClient:
    # A refused localhost connection costs seconds on some machines, and every
    # question makes two completions — so a failure is remembered briefly instead
    # of re-probing the whole request.
    COOLDOWN_SECONDS = 30

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._unreachable_until = 0.0

    @property
    def base_url(self) -> str:
        return self.settings.ollama_base_url.rstrip("/")

    def complete(self, system: str, prompt: str) -> Completion:
        if time.monotonic() < self._unreachable_until:
            raise LLMUnavailableError(
                f"Ollama at {self.base_url} is still in a {self.COOLDOWN_SECONDS}s cooldown after a failed call"
            )

        payload = {
            "model": self.settings.ollama_model,
            "system": system,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": self.settings.llm_temperature},
        }
        started = time.perf_counter()
        try:
            response = httpx.post(
                f"{self.base_url}/api/generate", json=payload, timeout=self.settings.llm_timeout_seconds
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # The server answered, so no cooldown is warranted — but Ollama replies
            # 404 when the requested model is not in its library, and a raw "404 Not
            # Found" reads like a broken endpoint rather than a missing download.
            if exc.response.status_code == 404:
                raise LLMUnavailableError(
                    f"{self.settings.ollama_model} is not in Ollama's library yet — "
                    f"run `ollama pull {self.settings.ollama_model}`"
                ) from exc
            raise LLMUnavailableError(
                f"Ollama at {self.base_url} returned HTTP {exc.response.status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            self._unreachable_until = time.monotonic() + self.COOLDOWN_SECONDS
            raise LLMUnavailableError(f"Ollama at {self.base_url} is unreachable: {exc}") from exc
        text = (response.json().get("response") or "").strip()
        if not text:
            raise LLMUnavailableError("Ollama returned an empty completion")
        return Completion(
            text=text,
            model=self.settings.ollama_model,
            provider="ollama",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    def health(self) -> dict[str, object]:
        if time.monotonic() < self._unreachable_until:
            remaining = int(self._unreachable_until - time.monotonic())
            return {
                "status": "unavailable",
                "detail": f"in cooldown for another {remaining}s",
                "expected": self.settings.ollama_model,
            }
        try:
            response = httpx.get(f"{self.base_url}/api/tags", timeout=5)
            response.raise_for_status()
            self._unreachable_until = 0.0
            models = [model.get("name") for model in response.json().get("models", [])]
            if self.settings.ollama_model in models:
                return {"status": "ok", "models": models, "expected": self.settings.ollama_model}
            return {
                "status": "no_model",
                "models": models,
                "expected": self.settings.ollama_model,
                "detail": f"Ollama is running but {self.settings.ollama_model} is not pulled yet — "
                f"run `ollama pull {self.settings.ollama_model}`",
            }
        except httpx.HTTPError as exc:
            self._unreachable_until = time.monotonic() + self.COOLDOWN_SECONDS
            return {"status": "unavailable", "detail": str(exc), "expected": self.settings.ollama_model}


_client: OllamaClient | None = None


def get_llm_client() -> OllamaClient:
    global _client
    if _client is None:
        _client = OllamaClient()
    return _client
