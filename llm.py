"""Minimal async client for LM Studio's OpenAI-compatible API."""
import logging
import re
import time

import aiohttp

log = logging.getLogger("llm")

THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
MODEL_RECHECK_SECONDS = 30


class LLMError(RuntimeError):
    pass


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
        self.params = {k: cfg[k] for k in self.PARAM_KEYS if k in cfg and cfg[k] not in ("", None)}
        self.params.update(cfg.get("extra_params") or {})
        self.timeout = aiohttp.ClientTimeout(total=cfg.get("timeout_seconds", 300))
        self._session: aiohttp.ClientSession | None = None
        self._model: tuple[str, bool] | None = None  # (model id, accepts images)
        self._checked_at = 0.0

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def model_info(self) -> tuple[str, bool]:
        """(model id, accepts images): the configured model, or else whichever chat model LM Studio has loaded.
        Re-checked every MODEL_RECHECK_SECONDS, so swapping models in LM Studio is picked up without a restart."""
        if self._model and time.monotonic() - self._checked_at < MODEL_RECHECK_SECONDS:
            return self._model
        try:
            info = await self._lookup_model()
        except (aiohttp.ClientError, LLMError):
            if self._model:  # LM Studio briefly unreachable: keep using what we know
                return self._model
            raise
        if info != self._model:
            log.info("LM Studio model: %s (%s)", info[0], "can see images" if info[1] else "text only")
        self._model, self._checked_at = info, time.monotonic()
        return info

    async def _lookup_model(self) -> tuple[str, bool]:
        http = await self._http()
        models = []
        try:  # LM Studio's own REST API knows which models are loaded and which are vision models
            async with http.get(f"{self.root}/api/v0/models") as r:
                if r.status == 200:
                    models = [m for m in (await r.json())["data"] if m.get("type") in ("llm", "vlm")]
        except aiohttp.ClientError:
            pass
        if self.configured_model:
            known = next((m for m in models if m["id"] == self.configured_model), None)
            return self.configured_model, known is None or known.get("type") == "vlm"
        loaded = [m for m in models if m.get("state") == "loaded"]
        if loaded:
            return loaded[0]["id"], loaded[0].get("type") == "vlm"
        async with http.get(f"{self.api}/models") as r:  # plain OpenAI API: no load state or vision info
            r.raise_for_status()
            ids = [m["id"] for m in (await r.json())["data"] if "embed" not in m["id"].lower()]
        if not ids:
            raise LLMError(f"No chat model available at {self.api}")
        return ids[0], True

    async def resolve_model(self) -> str:
        return (await self.model_info())[0]

    async def supports_images(self) -> bool:
        return (await self.model_info())[1]

    async def chat_message(self, messages: list[dict], **overrides) -> dict:
        """One completion. Returns {"content": text, "tool_calls": [...]}; pass tools=[...] to offer tools."""
        payload = {"model": await self.resolve_model(), "messages": messages, "stream": False,
                   **self.params, **overrides}
        http = await self._http()
        async with http.post(f"{self.api}/chat/completions", json=payload) as r:
            if r.status != 200:
                raise LLMError(f"LM Studio returned {r.status}: {(await r.text())[:500]}")
            data = await r.json()
        choice = data["choices"][0]
        if choice.get("finish_reason") == "length":
            log.warning("Reply hit max_tokens (%s); consider raising llm.max_tokens",
                        payload.get("max_tokens"))
        message = choice["message"]
        return {"content": strip_thinking(message.get("content") or ""), "tool_calls": message.get("tool_calls") or []}

    async def chat(self, messages: list[dict], **overrides) -> str:
        return (await self.chat_message(messages, **overrides))["content"]
