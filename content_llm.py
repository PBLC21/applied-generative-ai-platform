# content_llm.py
# Minimal, robust LLM wrapper for STAAR AI.
# - Works with OpenAI Python SDK v1.x (preferred) and v0.x (legacy).
# - Exposes: complete(), chat(), run(), and __call__ (all equivalent here).
# - Reads API key/model from environment variables:
#       OPENAI_API_KEY   (required)
#       OPENAI_MODEL     (optional; default: "gpt-4o-mini")
#       OPENAI_ORG       (optional)
#       OPENAI_BASE_URL  (optional; e.g., for proxies)
#
# Usage:
#   from content_llm import LLMClient
#   client = LLMClient()
#   text = client.complete("Say READY.", max_tokens=5, temperature=0.0)

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name)
    return v if v is not None and v != "" else default


class LLMClient:
    """
    Thin compatibility wrapper around OpenAI Chat Completions.

    Methods:
        complete(prompt: str, max_tokens=800, temperature=0.2, **kwargs) -> str
        chat(prompt: str, max_tokens=800, temperature=0.2, **kwargs) -> str
        run(prompt: str, max_tokens=800, temperature=0.2, **kwargs) -> str
        __call__(prompt: str, max_tokens=800, temperature=0.2, **kwargs) -> str
    """

    def __init__(
        self,
        model: Optional[str] = None,
        system: Optional[str] = None,
    ) -> None:
        api_key = _env("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is missing. Set it in your environment or .env file.")

        self.model = model or _env("OPENAI_MODEL", "gpt-4o-mini")
        self.system = system or "You are a helpful, concise assistant that returns Markdown only."
        self.api_key = api_key

        # Optional extras
        self.org = _env("OPENAI_ORG")
        self.base_url = _env("OPENAI_BASE_URL") or _env("OPENAI_API_BASE")

        # Try new SDK (v1.x). If unavailable, fall back to legacy (v0.x).
        self._mode = None
        try:
            from openai import OpenAI  # type: ignore
            kwargs: Dict[str, Any] = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            if self.org:
                kwargs["organization"] = self.org
            self._client_v1 = OpenAI(**kwargs)  # stores key internally
            self._mode = "v1"
        except Exception:
            # Legacy
            try:
                import openai  # type: ignore
                self._client_v0 = openai
                self._client_v0.api_key = self.api_key
                if self.org:
                    self._client_v0.organization = self.org
                if self.base_url:
                    # Legacy SDK uses api_base
                    self._client_v0.api_base = self.base_url  # type: ignore[attr-defined]
                self._mode = "v0"
            except Exception as e:
                raise RuntimeError(f"Failed to initialize OpenAI client: {e}") from e

    # --- Public API (all four methods behave the same) ---
    def complete(self, prompt: str, max_tokens: int = 800, temperature: float = 0.2, **kwargs: Any) -> str:
        return self._chat_once(prompt, max_tokens=max_tokens, temperature=temperature, **kwargs)

    def chat(self, prompt: str, max_tokens: int = 800, temperature: float = 0.2, **kwargs: Any) -> str:
        return self._chat_once(prompt, max_tokens=max_tokens, temperature=temperature, **kwargs)

    def run(self, prompt: str, max_tokens: int = 800, temperature: float = 0.2, **kwargs: Any) -> str:
        return self._chat_once(prompt, max_tokens=max_tokens, temperature=temperature, **kwargs)

    def __call__(self, prompt: str, max_tokens: int = 800, temperature: float = 0.2, **kwargs: Any) -> str:
        return self._chat_once(prompt, max_tokens=max_tokens, temperature=temperature, **kwargs)

    # --- Internal helpers ---
    def _chat_once(
        self,
        prompt: str,
        *,
        max_tokens: int = 800,
        temperature: float = 0.2,
        messages: Optional[List[Dict[str, str]]] = None,
        **kwargs: Any,
    ) -> str:
        """
        Sends a single-turn chat completion request. If `messages` is provided,
        it is used as-is; otherwise, we build a simple [system, user] message list.
        """
        if messages is None:
            messages = [{"role": "system", "content": self.system},
                        {"role": "user", "content": prompt}]
        # v1 SDK path
        if self._mode == "v1":
            resp = self._client_v1.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                **kwargs,
            )
            # v1 returns .choices[0].message.content
            return (resp.choices[0].message.content or "").strip()

        # v0 (legacy) path
        elif self._mode == "v0":
            # Legacy: openai.ChatCompletion.create
            resp = self._client_v0.ChatCompletion.create(  # type: ignore[attr-defined]
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                **kwargs,
            )
            # Legacy returns dict
            choice = resp["choices"][0]
            if "message" in choice and "content" in choice["message"]:
                return (choice["message"]["content"] or "").strip()
            # Some legacy deployments may return "text"
            return (choice.get("text", "") or "").strip()

        raise RuntimeError("OpenAI client not initialized properly.")

