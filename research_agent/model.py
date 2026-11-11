"""Thin Ollama adapter; no hosted models or automatic downloads."""

import asyncio
from urllib.parse import urlparse

import httpx
from ollama import AsyncClient, ResponseError

from .runtime import BudgetExceeded, Runtime
from .trace import Trace


class ModelError(Exception):
    pass


class OllamaModel:
    def __init__(self, model: str, host: str, runtime: Runtime, trace: Trace):
        parsed = urlparse(host)
        if (parsed.scheme not in {"http", "https"} or parsed.hostname not in
                {"localhost", "127.0.0.1", "::1"} or parsed.username or parsed.password):
            raise ValueError("Ollama URL must point to localhost without credentials")
        self.name = model
        self.runtime = runtime
        self.trace = trace
        self.client = AsyncClient(host=host, timeout=18, trust_env=False)
        self.http = httpx.AsyncClient(base_url=host, timeout=5, trust_env=False)
        self.supports_thinking = False

    async def preflight(self):
        try:
            async with asyncio.timeout(min(5, self.runtime.remaining())):
                info = await self.client.show(self.name)
                version_response = await self.http.get("/api/version")
                version_response.raise_for_status()
                version = version_response.json()
            caps = info.get("capabilities", [])
            if "tools" not in caps:
                raise ModelError(f"Model {self.name} does not advertise tool calling support")
            self.supports_thinking = "thinking" in caps
            self.trace.event("model_info", "preflight", model=self.name,
                             ollama_version=version.get("version", "unknown"),
                             capabilities=caps)
        except ResponseError as exc:
            if exc.status_code == 404:
                raise ModelError(f"Model {self.name} is missing. Run: ollama pull {self.name}") from exc
            raise ModelError("Ollama rejected the model check; inspect your local server") from exc
        except (httpx.HTTPError, ConnectionError, TimeoutError) as exc:
            raise ModelError("Cannot reach local Ollama. Start it with: ollama serve") from exc

    async def chat(self, messages: list[dict], *, node: str,
                   schema: dict | None = None, tools: list[dict] | None = None,
                   research: bool = False) -> dict:
        rt = self.runtime
        if rt.model_calls >= rt.max_models:
            raise BudgetExceeded("Model call budget reached")
        timeout = min(18, rt.remaining("research" if research else "model"))
        if timeout <= 0.05:
            raise BudgetExceeded("Model time budget reached")
        rt.model_calls += 1
        started = rt.elapsed
        self.trace.event("model_start", node, model=self.name, timeout_seconds=timeout)
        options = {"temperature": 0, "num_predict": 700 if schema else 180, "num_ctx": 8192}
        kwargs = {"model": self.name, "messages": messages, "stream": False,
                  "options": options, "keep_alive": "5m"}
        if schema is not None:
            kwargs["format"] = schema
        if tools:
            kwargs["tools"] = tools
        if self.supports_thinking:
            kwargs["think"] = False
        try:
            response = await rt.bounded(self.client.chat(**kwargs), timeout)
            message = response.message.model_dump(exclude_none=True)
            # Thinking is never stored in state/traces, even with a model override.
            message.pop("thinking", None)
            self.trace.event("model_end", node, status="ok",
                             duration_ms=round((rt.elapsed - started) * 1000, 2))
            return message
        except TimeoutError:
            self.trace.event("model_end", node, status="timeout",
                             duration_ms=round((rt.elapsed - started) * 1000, 2))
            raise
        except (ResponseError, httpx.HTTPError, ConnectionError) as exc:
            self.trace.event("model_end", node, status="error")
            raise ModelError("Local Ollama request failed; check the server/model") from exc

    async def close(self):
        await self.client._client.aclose()
        await self.http.aclose()
