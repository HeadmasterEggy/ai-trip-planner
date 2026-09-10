"""Test infrastructure: a chat model that answers from a script.

`create_agent` drives its loop through a real model, so testing the supervisor's
resilience and its streaming without a provider key needs a model that can be
told "fail once, then ask for these specialists". The last script entry repeats,
so a script can also say "keep delegating forever".

Kept out of a test module so more than one test module can import it; `tests/` has
no `__init__.py`, so pytest puts this directory on the path.
"""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field


class ScriptedChatModel(BaseChatModel):
    """Answers from `responses`: an `AIMessage`, or an exception to raise."""

    responses: list
    index: int = 0
    seen: list = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        # `create_agent` binds the tools before calling; a scripted model does not
        # need them.
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        item = self.responses[min(self.index, len(self.responses) - 1)]
        self.index += 1
        if isinstance(item, Exception):
            raise item
        return ChatResult(generations=[ChatGeneration(message=item)])


def tool_call(name: str, call_id: str = "c1") -> AIMessage:
    """An assistant turn that asks for one specialist tool."""
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": {}, "id": call_id, "type": "tool_call"}],
    )


def tool_calls(*names: str) -> AIMessage:
    """An assistant turn that asks for several specialists at once."""
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": {}, "id": f"c{index}", "type": "tool_call"}
            for index, name in enumerate(names)
        ],
    )
