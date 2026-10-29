"""Minimal async client for LM Studio's OpenAI-compatible API."""
import asyncio
import logging
import re
import time
from dataclasses import dataclass

import aiohttp

log = logging.getLogger("llm")

THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
# how LM Studio (llama.cpp, MLX) words "the prompt doesn't fit in the loaded context"
OVERFLOW_RE = re.compile(r"context (length|window|size)|n_ctx|context.{0,30}overflow|too many tokens",
                         re.IGNORECASE)
MODEL_RECHECK_SECONDS = 30


class LLMError(RuntimeError):
    pass


class ContextOverflow(LLMError):
    """The prompt is too long for the model's context window."""


@dataclass(frozen=True)
class ModelInfo:
    id: str
    vision: bool
    context: int | None  # tokens the loaded model takes in (prompt + reply), if LM Studio says


def strip_thinking(text: str) -> str:
    """Remove reasoning blocks some models leave inline in the content."""
    text = THINK_RE.sub("", text)
    if "</think>" in text:  # opening tag was eaten by the chat template
        text = text.split("</think>", 1)[1]
    return text.strip()


class LMStudioClient:
    PARAM_KEYS = ("temperature", "top_p", "top_k", "max_tokens", "presence_penalty",
                  "frequency_penalty", "repeat_penalty", "reasoning_effort")

    def __init__(self, cfg: dict):
        root = cfg["base_url"].rstrip("/")
        self.root = root[:-3] if root.endswith("/v1") else root
        self.api = self.root + "/v1"
        self.configured_model = cfg.get("model") or ""
        self.configured_context = cfg.get("context_length") or 0  # 0: ask LM Studio
        self.params = {k: cfg[k] for k in self.PARAM_KEYS if k in cfg and cfg[k] not in ("", None)}
        self.params.update(cfg.get("extra_params") or {})
        self.timeout = aiohttp.ClientTimeout(total=cfg.get("timeout_seconds", 300))
        self._session: aiohttp.ClientSession | None = None
        self.info: ModelInfo | None = None  # latest known, for the status screen
        self._checked_at = 0.0
        self.problem = ""                   # why the last request failed, until one succeeds

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def model_info(self) -> ModelInfo:
        """The configured model, or else whichever chat model LM Studio has loaded. Re-checked every
        MODEL_RECHECK_SECONDS, so swapping or reloading models in LM Studio is picked up without a restart."""
        if self.info and time.monotonic() - self._checked_at < MODEL_RECHECK_SECONDS:
            return self.info
        try:
            info = await self._lookup_model()
        except (aiohttp.ClientError, asyncio.TimeoutError, LLMError) as e:
            self.problem = f"can't reach LM Studio ({type(e).__name__})"
            if self.info:  # LM Studio briefly unreachable: keep using what we know
                return self.info
            raise
        if info != self.info:
            log.info("LM Studio model: %s (%s, %s)", info.id, "can see images" if info.vision else "text only",
                     f"{info.context:,}-token context" if info.context else "context size unknown")
        self.info, self._checked_at = info, time.monotonic()
        return info

    async def _lookup_model(self) -> ModelInfo:
        http = await self._http()
        models = []
        try:  # LM Studio's own REST API knows which models are loaded, how big their context is, and vision
            async with http.get(f"{self.root}/api/v0/models") as r:
                if r.status == 200:
                    models = [m for m in (await r.json())["data"] if m.get("type") in ("llm", "vlm")]
        except aiohttp.ClientError:
            pass
        if self.configured_model:
            known = next((m for m in models if m["id"] == self.configured_model), None)
            return ModelInfo(self.configured_model, known is None or known.get("type") == "vlm",
                             known and known.get("loaded_context_length"))
        loaded = [m for m in models if m.get("state") == "loaded"]
        if loaded:
            return ModelInfo(loaded[0]["id"], loaded[0].get("type") == "vlm", loaded[0].get("loaded_context_length"))
        async with http.get(f"{self.api}/models") as r:  # plain OpenAI API: no load state or vision info
            r.raise_for_status()
            ids = [m["id"] for m in (await r.json())["data"] if "embed" not in m["id"].lower()]
        if not ids:
            raise LLMError(f"No chat model available at {self.api}")
        return ModelInfo(ids[0], True, None)

    async def resolve_model(self) -> str:
        return (await self.model_info()).id

    async def supports_images(self) -> bool:
        return (await self.model_info()).vision

    async def context_limit(self) -> int | None:
        """How many tokens fit in the model's context, or None if nobody knows."""
        if self.configured_context:
            return self.configured_context
        try:
            return (await self.model_info()).context
        except (aiohttp.ClientError, asyncio.TimeoutError, LLMError):
            return None

    async def chat_message(self, messages: list[dict], **overrides) -> dict:
        """One completion. Returns {"content": text, "tool_calls": [...], "usage": {...}, "seconds": float};
        pass tools=[...] to offer tools."""
        payload = {"model": await self.resolve_model(), "messages": messages, "stream": False,
                   **self.params, **overrides}
        http = await self._http()
        started = time.monotonic()
        try:
            async with http.post(f"{self.api}/chat/completions", json=payload) as r:
                if r.status != 200:
                    text = (await r.text())[:500]
                    error = ContextOverflow if OVERFLOW_RE.search(text) else LLMError
                    self.problem = "the prompt didn't fit" if error is ContextOverflow else f"error {r.status}"
                    raise error(f"LM Studio returned {r.status}: {text}")
                data = await r.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            self.problem = f"can't reach LM Studio ({type(e).__name__})"
            raise
        self.problem = ""
        choice = data["choices"][0]
        if choice.get("finish_reason") == "length":
            log.warning("Reply hit max_tokens (%s); consider raising llm.max_tokens",
                        payload.get("max_tokens"))
        message = choice["message"]
        return {"content": strip_thinking(message.get("content") or ""), "tool_calls": message.get("tool_calls") or [],
                "usage": data.get("usage") or {}, "seconds": time.monotonic() - started}

    async def chat(self, messages: list[dict], **overrides) -> str:
        return (await self.chat_message(messages, **overrides))["content"]
