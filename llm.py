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
    arch: str = ""       # e.g. "qwen35", "gemma3", "gpt-oss", if LM Studio says


@dataclass(frozen=True)
class PromptFormat:
    """How a model family marks chat turns. In text mode the bot writes the whole prompt with these, instead of
    LM Studio applying the model's own chat template."""
    name: str
    starts: dict[str, str]  # role -> what opens a turn
    end: str                # what closes one
    stop: tuple[str, ...]   # in case LM Studio hands these back as text instead of stopping on them
    # for a model that reasons before it answers:
    think_open: str = ""    # opens the reasoning...
    think_close: str = ""   # ...and closes it
    no_think: str = ""      # an empty reasoning block, which tells it to answer straight away
    think_on: str = ""      # goes at the top of the system turn to switch reasoning on

    @property
    def thinks(self) -> bool:
        return bool(self.think_open)

    def render(self, messages: list[dict], think: bool = False) -> str:
        return "".join(self.starts[m["role"]] + (self.think_on if think and m["role"] == "system" else "")
                       + m["content"] + self.end for m in messages)


CHATML = {role: f"<|im_start|>{role}\n" for role in ("system", "user", "assistant")}
FORMATS = {f.name: f for f in (
    PromptFormat("chatml", CHATML, "<|im_end|>\n", ("<|im_end|>", "<|im_start|>")),
    PromptFormat("qwen", CHATML, "<|im_end|>\n", ("<|im_end|>", "<|im_start|>"),
                 "<think>\n", "\n</think>\n\n", "<think>\n\n</think>\n\n"),
    PromptFormat("llama3", {role: f"<|start_header_id|>{role}<|end_header_id|>\n\n"
                            for role in ("system", "user", "assistant")},
                 "<|eot_id|>", ("<|eot_id|>", "<|start_header_id|>")),
    # Gemma 2 and 3 have no system turn: the system prompt goes in a user turn of its own
    PromptFormat("gemma", {"system": "<start_of_turn>user\n", "user": "<start_of_turn>user\n",
                           "assistant": "<start_of_turn>model\n"},
                 "<end_of_turn>\n", ("<end_of_turn>", "<start_of_turn>")),
    PromptFormat("gemma4", {"system": "<|turn>system\n", "user": "<|turn>user\n", "assistant": "<|turn>model\n"},
                 "<turn|>\n", ("<turn|>", "<|turn>"),
                 "<|channel>thought\n", "\n<channel|>", "<|channel>thought\n<channel|>", "<|think|>\n"),
)}


def format_for(arch: str) -> PromptFormat | None:
    """The prompt format for a model architecture, for prompt_format "auto". Llama is left out: Mistral models
    use that architecture too, with a different format. Gemma is matched exactly, since Gemma 4 changed it."""
    arch = arch.lower()
    if arch.startswith("qwen"):
        return FORMATS["chatml" if arch.startswith("qwen2") else "qwen"]  # Qwen 3 and later think
    if arch in ("gemma", "gemma2", "gemma3", "gemma3n"):
        return FORMATS["gemma"]
    if arch == "gemma4":
        return FORMATS["gemma4"]
    return None


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
        self.prompt_format = cfg.get("prompt_format") or "chat"
        if self.prompt_format not in ("chat", "auto", *FORMATS):
            log.warning("Unknown llm.prompt_format %r (use chat, auto or %s); using chat", self.prompt_format,
                        ", ".join(FORMATS))
            self.prompt_format = "chat"
        self.format = FORMATS.get(self.prompt_format)  # None: chat mode, LM Studio applies the chat template
        self._format_model = ""                        # the model "auto" last picked a format for
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
                             known and known.get("loaded_context_length"), (known or {}).get("arch") or "")
        loaded = [m for m in models if m.get("state") == "loaded"]
        if loaded:
            return ModelInfo(loaded[0]["id"], loaded[0].get("type") == "vlm", loaded[0].get("loaded_context_length"),
                             loaded[0].get("arch") or "")
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

    async def text_format(self) -> PromptFormat | None:
        """The format to write prompts in (text mode), or None to send chat messages and let LM Studio apply
        the model's own chat template (chat mode)."""
        if self.prompt_format != "auto":
            return self.format
        info = await self.model_info()
        if info.id != self._format_model:
            self.format, self._format_model = format_for(info.arch), info.id
            log.info("Prompt format for %s: %s", info.id, self.format.name if self.format else
                     f"its own chat template (no format here for {info.arch or 'its'} models)")
        return self.format

    async def _post(self, path: str, payload: dict) -> tuple[dict, float]:
        """POST to the OpenAI-compatible API. Returns the response and how many seconds it took."""
        http = await self._http()
        started = time.monotonic()
        try:
            async with http.post(f"{self.api}/{path}", json=payload) as r:
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
        return data, time.monotonic() - started

    async def chat_message(self, messages: list[dict], **overrides) -> dict:
        """One completion. Returns {"content": text, "tool_calls": [...], "usage": {...}, "seconds": float};
        pass tools=[...] to offer tools."""
        payload = {"model": await self.resolve_model(), "messages": messages, "stream": False,
                   **self.params, **overrides}
        data, seconds = await self._post("chat/completions", payload)
        choice = data["choices"][0]
        if choice.get("finish_reason") == "length":
            log.warning("Reply hit max_tokens (%s); consider raising llm.max_tokens",
                        payload.get("max_tokens"))
        message = choice["message"]
        return {"content": strip_thinking(message.get("content") or ""), "tool_calls": message.get("tool_calls") or [],
                "usage": data.get("usage") or {}, "seconds": seconds}

    async def text_message(self, prompt: str, **overrides) -> dict:
        """One raw text completion that continues `prompt` (text mode: no chat template, no tools, no images).
        Returns {"content": text, "usage": {...}, "seconds": float}."""
        payload = {"model": await self.resolve_model(), "prompt": prompt, "stream": False,
                   **self.params, **overrides}
        payload.pop("reasoning_effort", None)  # thinking is written into the prompt instead
        data, seconds = await self._post("completions", payload)
        choice = data["choices"][0]
        if choice.get("finish_reason") == "length" and "max_tokens" not in overrides:
            log.warning("Reply hit max_tokens (%s); consider raising llm.max_tokens",
                        payload.get("max_tokens"))
        return {"content": choice.get("text") or "", "usage": data.get("usage") or {}, "seconds": seconds}

    async def chat(self, messages: list[dict], **overrides) -> str:
        return (await self.chat_message(messages, **overrides))["content"]
