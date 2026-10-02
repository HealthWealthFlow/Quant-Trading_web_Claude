"""AI provider adapters (spec §88). The research pipeline talks only to `Provider`; no provider is hard-coded.

- DeepSeek (default) and OpenAI: OpenAI-compatible chat-completions HTTP API with JSON response mode.
- Anthropic (Claude): official `anthropic` SDK (optional extra), used e.g. as the second-opinion provider (§89).

API keys come from environment variables only and are never logged or stored (spec §13–§15).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from ..config import get_secret
from ..logging_setup import redact


@dataclass
class ProviderResponse:
    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    model: str
    truncated: bool = False  # finish_reason == "length": the answer was cut off at max_tokens


class ProviderError(RuntimeError):
    pass


class ProviderRefusal(ProviderError):
    pass


class Provider:
    name = "base"

    def complete(self, model: str, system: str, user: str, max_tokens: int) -> ProviderResponse:
        raise NotImplementedError


class OpenAICompatibleProvider(Provider):
    """DeepSeek and OpenAI both expose POST {base_url}/chat/completions."""

    def __init__(self, name: str, base_url: str, api_key_env: str, timeout: float = 120.0,
                 transport: httpx.BaseTransport | None = None, disable_thinking: bool = False):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.disable_thinking = disable_thinking
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def complete(self, model: str, system: str, user: str, max_tokens: int) -> ProviderResponse:
        key = get_secret(self.api_key_env)
        if not key:
            raise ProviderError(f"{self.name}: {self.api_key_env} is not set")
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max_tokens,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        if self.disable_thinking:
            # Extraction is deterministic JSON, not reasoning. Thinking tokens are billed and are drawn from the
            # same max_tokens budget, so leaving it on truncates the JSON before it is complete.
            body["thinking"] = {"type": "disabled"}
        started = time.monotonic()
        try:
            r = self._client.post(f"{self.base_url}/chat/completions", json=body,
                                  headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError as e:
            raise ProviderError(redact(f"{self.name}: {type(e).__name__}: {e}")) from None
        if r.status_code >= 400:
            raise ProviderError(redact(f"{self.name}: HTTP {r.status_code}: {r.text[:300]}"))
        data = r.json()
        choice = (data.get("choices") or [{}])[0]
        usage = data.get("usage") or {}
        return ProviderResponse(
            text=(choice.get("message") or {}).get("content") or "",
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            latency_ms=int((time.monotonic() - started) * 1000),
            model=data.get("model") or model,
            truncated=choice.get("finish_reason") == "length",
        )


class AnthropicProvider(Provider):
    """Claude via the official SDK. Server-side refusal fallback is enabled ("default" routing)."""

    name = "anthropic"

    def __init__(self, effort: str = "high"):
        self.effort = effort
        self._client = None

    def _get_client(self):
        if self._client is None:
            key = get_secret("ANTHROPIC_API_KEY")
            if not key:
                raise ProviderError("anthropic: ANTHROPIC_API_KEY is not set")
            try:
                import anthropic
            except ImportError as e:
                raise ProviderError("anthropic: install the optional extra: pip install -e '.[anthropic]'") from e
            self._client = anthropic.Anthropic(api_key=key)
        return self._client

    def complete(self, model: str, system: str, user: str, max_tokens: int) -> ProviderResponse:
        import anthropic

        client = self._get_client()
        started = time.monotonic()
        try:
            resp = client.beta.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": self.effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.RateLimitError as e:
            raise ProviderError(f"anthropic: rate limited: {e}") from None
        except anthropic.APIStatusError as e:
            raise ProviderError(redact(f"anthropic: HTTP {e.status_code}: {e}")) from None
        except anthropic.APIConnectionError as e:
            raise ProviderError(f"anthropic: connection error: {e}") from None
        if resp.stop_reason == "refusal":
            raise ProviderRefusal("anthropic: request declined by the model's safety system")
        text = "".join(b.text for b in resp.content if b.type == "text")
        # usage.iterations is the per-attempt billing record when a fallback ran; sum it when present.
        iterations = getattr(resp.usage, "iterations", None) or []
        if iterations:
            in_tok = sum(int(getattr(i, "input_tokens", 0) or 0) for i in iterations)
            out_tok = sum(int(getattr(i, "output_tokens", 0) or 0) for i in iterations)
        else:
            in_tok, out_tok = resp.usage.input_tokens, resp.usage.output_tokens
        return ProviderResponse(text=text, input_tokens=in_tok, output_tokens=out_tok,
                                latency_ms=int((time.monotonic() - started) * 1000), model=resp.model,
                                truncated=resp.stop_reason == "max_tokens")


def default_providers() -> dict[str, Provider]:
    return {
        # DeepSeek's model supports a thinking mode that is on by default; extraction needs plain JSON.
        "deepseek": OpenAICompatibleProvider("deepseek", "https://api.deepseek.com", "DEEPSEEK_API_KEY",
                                             disable_thinking=True),
        "openai": OpenAICompatibleProvider("openai", "https://api.openai.com/v1", "OPENAI_API_KEY"),
        "anthropic": AnthropicProvider(),
    }
