"""The model layer: one `complete(messages, tools)` interface over every provider.

The Ask pipeline, agent loop and eval harness only ever call `Model.complete`. Messages and
tool schemas use one neutral format, translated here per provider:

    {"role": "user", "content": str}
    {"role": "assistant", "content": str, "tool_calls": [ToolCall], "provider_content": ...}
    {"role": "tool", "tool_call_id": str, "name": str, "content": str, "is_error": bool}

Specs: "anthropic:<model id>" (Claude, the baseline) or "openai:<model>" (any
OpenAI-compatible server, e.g. vLLM or Ollama, at OPENAI_BASE_URL).
"""

import json
import os
import re
import time
from dataclasses import dataclass, field
from functools import cache
from typing import Protocol

# USD per million input / output tokens (Anthropic list prices, 2026-09).
PRICES = {
    "claude-sonnet-5": (2.00, 10.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-opus-5-5": (4.00, 20.00),
}


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON schema for the arguments


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict
    error: str | None = None  # set when the model's arguments weren't valid JSON

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "arguments": self.arguments, "error": self.error}


@dataclass
class Response:
    text: str
    tool_calls: list[ToolCall]
    input_tokens: int
    output_tokens: int
    latency_s: float
    model: str
    stop_reason: str
    provider_content: list | None = None
    cost_usd: float = 0.0

    def message(self) -> dict:
        """This response as a neutral assistant message, to append to the conversation."""
        return {
            "role": "assistant",
            "content": self.text,
            "tool_calls": self.tool_calls,
            "provider_content": self.provider_content,
        }


class Model(Protocol):
    name: str

    def complete(
        self,
        messages: list[dict],
        tools: list[ToolSpec] | None = None,
        system: str | None = None,
        max_tokens: int = 8000,
    ) -> Response: ...


@dataclass
class AnthropicModel:
    model: str
    name: str = field(init=False)

    def __post_init__(self):
        import anthropic

        self.name = f"anthropic:{self.model}"
        self._client = anthropic.Anthropic(max_retries=4)

    def complete(self, messages, tools=None, system=None, max_tokens=8000) -> Response:
        kwargs = {}
        if tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ]
        if system:
            kwargs["system"] = system
        start = time.monotonic()
        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=_anthropic_messages(messages),
            **kwargs,
        )
        latency = time.monotonic() - start
        if response.stop_reason == "refusal":
            raise ModelError(f"{self.name} refused the request")
        text = "".join(b.text for b in response.content if b.type == "text")
        calls = [
            ToolCall(b.id, b.name, dict(b.input)) for b in response.content if b.type == "tool_use"
        ]
        price_in, price_out = PRICES.get(self.model, (0.0, 0.0))
        usage = response.usage
        input_tokens = (
            usage.input_tokens
            + (usage.cache_read_input_tokens or 0)
            + (usage.cache_creation_input_tokens or 0)
        )
        return Response(
            text=text,
            tool_calls=calls,
            input_tokens=input_tokens,
            output_tokens=usage.output_tokens,
            latency_s=latency,
            model=self.name,
            stop_reason=response.stop_reason or "",
            # Replayed verbatim so thinking blocks stay valid in later turns.
            provider_content=[b.to_dict() for b in response.content],
            cost_usd=(input_tokens * price_in + usage.output_tokens * price_out) / 1e6,
        )


def _anthropic_messages(messages: list[dict]) -> list[dict]:
    result: list[dict] = []
    for m in messages:
        if m["role"] == "user":
            result.append({"role": "user", "content": m["content"]})
        elif m["role"] == "assistant":
            content = m.get("provider_content") if _is_anthropic(m) else None
            if content is None:
                content = ([{"type": "text", "text": m["content"]}] if m["content"] else []) + [
                    {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                    for c in m.get("tool_calls", [])
                ]
            result.append({"role": "assistant", "content": content})
        elif m["role"] == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": m["tool_call_id"],
                "content": m["content"],
                "is_error": m.get("is_error", False),
            }
            # All results for one assistant turn go back in a single user message.
            if result and result[-1]["role"] == "user" and isinstance(result[-1]["content"], list):
                result[-1]["content"].append(block)
            else:
                result.append({"role": "user", "content": [block]})
    return result


def _is_anthropic(message: dict) -> bool:
    content = message.get("provider_content")
    return isinstance(content, list) and all(isinstance(b, dict) and "type" in b for b in content)


@dataclass
class OpenAICompatibleModel:
    """Open models behind vLLM or Ollama (both speak the OpenAI chat + tool-calling API)."""

    model: str
    gpu_hourly_usd: float = 0.0
    base_url: str = "http://localhost:8001/v1"
    thinking: bool | None = None  # Qwen-style hybrid reasoning: False turns thinking off
    name: str = field(init=False)

    def __post_init__(self):
        import openai

        self.name = f"openai:{self.model}"
        self._client = openai.OpenAI(
            base_url=self.base_url,
            api_key=os.environ.get("OPENAI_API_KEY", "not-needed"),
            max_retries=2,
            timeout=600,
        )
        self._extra = (
            {"chat_template_kwargs": {"enable_thinking": self.thinking}}
            if self.thinking is not None
            else {}
        )

    def complete(self, messages, tools=None, system=None, max_tokens=8000) -> Response:
        kwargs = {}
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
        chat = ([{"role": "system", "content": system}] if system else []) + _openai_messages(
            messages
        )
        start = time.monotonic()
        response = self._client.chat.completions.create(
            model=self.model,
            messages=chat,
            max_tokens=max_tokens,
            temperature=0,
            extra_body=self._extra or None,
            **kwargs,
        )
        latency = time.monotonic() - start
        choice = response.choices[0]
        calls = []
        for call in choice.message.tool_calls or []:
            try:
                arguments, error = json.loads(call.function.arguments or "{}"), None
            except json.JSONDecodeError as e:
                arguments, error = {}, f"invalid JSON arguments: {e}"
            calls.append(ToolCall(call.id, call.function.name, arguments, error))
        usage = response.usage
        return Response(
            text=strip_reasoning(choice.message.content or ""),
            tool_calls=calls,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            latency_s=latency,
            model=self.name,
            stop_reason=choice.finish_reason or "",
            cost_usd=latency * self.gpu_hourly_usd / 3600,
        )


def _openai_messages(messages: list[dict]) -> list[dict]:
    result = []
    for m in messages:
        if m["role"] == "user":
            result.append({"role": "user", "content": m["content"]})
        elif m["role"] == "assistant":
            message = {"role": "assistant", "content": m["content"] or ""}
            if m.get("tool_calls"):
                message["tool_calls"] = [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                    }
                    for c in m["tool_calls"]
                ]
            result.append(message)
        elif m["role"] == "tool":
            result.append(
                {"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]}
            )
    return result


def strip_reasoning(text: str) -> str:
    """Drop <think>…</think> blocks some open models leave in their visible output."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


class ModelError(RuntimeError):
    pass


@cache
def get_model(
    spec: str,
    gpu_hourly_usd: float = 0.0,
    base_url: str = "http://localhost:8001/v1",
    thinking: bool | None = None,
) -> Model:
    provider, _, model = spec.partition(":")
    if provider == "anthropic":
        return AnthropicModel(model)
    if provider == "openai":
        return OpenAICompatibleModel(model, gpu_hourly_usd, base_url, thinking)
    raise ValueError(f"Unknown model spec {spec!r} (use anthropic:<id> or openai:<model>)")


def load_model(settings, spec: str | None = None) -> Model:
    """The configured model (or `spec`), with the settings' server, thinking and GPU rate."""
    return get_model(
        spec or settings.model, settings.gpu_hourly_usd, settings.base_url, settings.thinking
    )


def parse_json(text: str) -> dict:
    """The JSON object in a model's reply, tolerating code fences and surrounding prose."""
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in the reply")
    return json.loads(candidate[start : end + 1])
