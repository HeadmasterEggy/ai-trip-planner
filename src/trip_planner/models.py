"""Provider selection and structured-output adaptation.

Ported from `packages/agents/src/models.ts`, including the provider quirks that
were expensive to find:

- MiniMax runs two independent account systems. Mainland-China keys work
  against api.minimaxi.com and return 401 "invalid api key (2049)" against the
  international api.minimax.io, and vice versa.
- MiniMax silently ignores a forced tool_choice: it answers in prose with no
  tool call, which is indistinguishable from an auth failure at the call site.
  With tool_choice "auto" it emits a well-formed call.
- Both providers treat a JSON schema's numeric constraints as advisory, so the
  specialists restate their hard limits in the prompt as well.
- DeepSeek rejects LangChain's default json_schema response format, so
  structured output goes through tool calling instead.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any, TypeVar

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ValidationError

Schema = TypeVar("Schema", bound=BaseModel)

# Every task routes to DeepSeek: MiniMax produced comparable drafts but took
# 15-25s per structured call against ~7s, tripling page latency. The MiniMax
# branch stays wired so a task can be routed back by editing this map.
MODEL_ROUTING: dict[str, str] = {
    "itinerary": "deepseek",
    "destination-guide": "deepseek",
    "dining": "deepseek",
    "transport": "deepseek",
    "accommodation": "deepseek",
}


def create_routed_chat_model(task: str) -> ChatOpenAI | None:
    """Build the chat model for a task, or None when its credentials are absent.

    Returning None rather than raising is what lets the whole app run offline:
    each specialist falls back to deterministic output.
    """
    provider = MODEL_ROUTING.get(task, "deepseek")
    if provider == "deepseek":
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            return None
        return ChatOpenAI(
            api_key=api_key,
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
            temperature=0,
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        )

    api_key = os.getenv("MINIMAX_API_KEY")
    if not api_key:
        return None
    return ChatOpenAI(
        api_key=api_key,
        model=os.getenv("MINIMAX_MODEL", "MiniMax-M2.7"),
        # MiniMax rejects temperature 0.
        temperature=0.1,
        base_url=os.getenv("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1"),
    )


def _with_correction(prompt: str, name: str, error: Exception) -> str:
    """Feed the failure back so the model can repair its own output once."""
    return (
        f"{prompt}\n\nA previous attempt failed. Return the {name} payload again and fix "
        f"exactly these problems, respecting every type, minimum and maximum in the schema:\n"
        f"{error}"
    )


def create_structured_invoker(
    task: str, schema: type[Schema], name: str
) -> Callable[[str], Schema] | None:
    """Return a prompt -> validated-model callable, or None without credentials.

    One corrective retry is built in: both providers routinely overrun a string
    or array limit on the first attempt, and re-asking with the validation error
    recovers most of those instead of dropping to the deterministic fallback.
    """
    model = create_routed_chat_model(task)
    if model is None:
        return None

    # Tool calling, not response_format: DeepSeek rejects the json_schema
    # response format LangChain reaches for by default with
    # "This response_format type is unavailable now".
    structured = model.with_structured_output(schema, method="function_calling")

    def call(prompt: str) -> Schema:
        result: Any = structured.invoke(prompt)
        if result is None:
            # The provider answered without emitting a tool call. Parsing None
            # against the schema would report a schema bug; name what happened.
            raise ValueError(f"{name}: the model returned no structured result.")
        return result if isinstance(result, schema) else schema.model_validate(result)

    def invoke(prompt: str) -> Schema:
        try:
            return call(prompt)
        except (ValidationError, ValueError) as error:
            return call(_with_correction(prompt, name, error))

    return invoke
