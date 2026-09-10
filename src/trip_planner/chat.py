"""Conversational entry point.

A message is turned into an explicit patch of the trip brief, the orchestrator
re-plans against the updated brief, and a reply is written from the resulting
plan.

The extraction is deliberately conservative: only fields the traveller actually
stated are changed. Inferring a date or a budget the user did not give is worse
than asking, because the plan then silently drifts from what they asked for.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, Field, ValidationError

from .contracts import (
    ChatRequest,
    ChatResponse,
    ChatTurn,
    ProgressEvent,
    TripBrief,
    TripPlan,
    brief_problem,
)
from .demo import DEMO_BRIEF
from .memory import memory as default_memory
from .models import create_routed_chat_model
from .workflow import OrchestratorOptions, PlanStream, run_orchestrator_stream

PATCH_FIELDS = ("destination", "dates", "groupSize", "budgetTotal", "nationality")


class BriefPatch(BaseModel):
    destination: str | None = None
    dates: tuple[str, str] | None = None
    groupSize: int | None = Field(default=None, gt=0)
    budgetTotal: float | None = Field(default=None, gt=0)
    nationality: str | None = None


class BriefExtractor(Protocol):
    def extract(self, message: str, current: TripBrief) -> BriefPatch: ...


# The wire shape asked of a model. Every field is nullable so "not mentioned"
# is expressible; the two dates are scalars because a tuple of primitives is
# awkward for strict JSON schema, and the public contract stays unchanged.
class _ModelPatch(BaseModel):
    destination: str | None = None
    startDate: str | None = None
    endDate: str | None = None
    groupSize: int | None = None
    budgetTotal: float | None = None
    nationality: str | None = None


def _amount(value: str) -> float | None:
    try:
        parsed = float(value.replace(",", ""))
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _clean_destination(value: str) -> str:
    value = re.sub(r"\s+(?:trip|travel|holiday)$", "", value.strip(), flags=re.IGNORECASE)
    return re.sub(r"[，,。.]+$", "", value).strip()


_CN_NUMBERS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def extract_brief_patch_locally(message: str) -> BriefPatch:
    """Parse explicit updates without a model.

    This is the fallback when no extraction model is configured and when one
    fails, so it has to handle the phrasings the UI actually suggests. It reads
    both English and Chinese because the reply generator answers in the
    traveller's language and the input follows suit.
    """
    patch: dict[str, Any] = {}

    dates = re.search(
        r"(\d{4}-\d{2}-\d{2})\s*(?:to|through|until|–|—|至|到)\s*(\d{4}-\d{2}-\d{2})",
        message,
        re.IGNORECASE,
    )
    if dates:
        patch["dates"] = (dates.group(1), dates.group(2))

    budget = re.search(
        r"(?:budget|预算(?:改成|调整为|是|为)?)[^\d]{0,12}(?:USD\s*)?\$?\s*([\d,]+(?:\.\d+)?)",
        message,
        re.IGNORECASE,
    ) or re.search(r"\$\s*([\d,]+(?:\.\d+)?)", message)
    if budget:
        patch["budgetTotal"] = _amount(budget.group(1))

    group = re.search(r"(\d+)\s*(?:people|persons?|travell?ers?|人)", message, re.IGNORECASE)
    if group:
        patch["groupSize"] = _amount(group.group(1))
    if not patch.get("groupSize"):
        cn = re.search(r"([一二两三四五六七八九十])\s*(?:个)?人", message)
        if cn:
            patch["groupSize"] = _CN_NUMBERS.get(cn.group(1))

    destination = ""
    # Ordered most-specific first: an explicit "trip to X" beats a bare leading
    # city name, which would otherwise swallow the rest of the sentence.
    english_trip = (
        r"(?:trip|travel|holiday|go|going)\s+(?:to|in)\s+(.+?)"
        r"(?=\s+(?:for|from|between|on|with|budget)\b|[,.;]|$)"
    )
    english_explicit = (
        r"(?:destination|place)(?:\s+(?:is|to|as))?\s*[:=]?\s+(.+?)"
        r"(?=\s+(?:and\s+)?(?:for|from|between|on|with|budget)\b|[,.;]|$)"
    )
    leading_city = r"^\s*([A-Za-z][A-Za-z &.\-]+?)\s*[,，]\s*\d{4}-\d{2}-\d{2}"
    chinese = (
        r"(?:去|前往|目的地(?:是|为|改成|调整为)?)[：:\s]*"
        r"([一-鿿A-Za-z][一-鿿A-Za-z&·\- ]*?)"
        r"(?=\s*(?:旅行|旅游|玩|，|,|。|预算|\d{4}-|$))"
    )
    for pattern, flags in (
        (english_trip, re.IGNORECASE),
        (english_explicit, re.IGNORECASE),
        (leading_city, 0),
        (chinese, 0),
    ):
        found = re.search(pattern, message, flags)
        if found:
            destination = _clean_destination(found.group(1))
            if destination:
                break
    if destination:
        patch["destination"] = destination

    passport = re.search(r"([A-Za-z][A-Za-z ]+?)\s+passport", message, re.IGNORECASE) or re.search(
        r"([一-鿿]{2,12})护照", message
    )
    if passport:
        patch["nationality"] = passport.group(1).strip()

    return BriefPatch(**{k: v for k, v in patch.items() if v is not None})


def apply_brief_patch(current: TripBrief, patch: BriefPatch, trip_id: str) -> TripBrief:
    """Merge a patch and re-validate the whole brief.

    Feasibility is checked here rather than in the specialists: a brief with an
    end before its start, or with more cities than nights, would otherwise fail
    deep inside a run with a message meant for a developer.
    """
    updates = patch.model_dump(exclude_none=True)
    nxt = TripBrief(**{**current.model_dump(), **updates, "tripId": trip_id})
    problem = brief_problem(nxt)
    if problem:
        raise ValueError(problem)
    return nxt


def _extraction_prompt(message: str, current: TripBrief) -> str:
    return (
        "Extract only explicit updates to the trip brief. Use null for every field the user did "
        "not specify. Do not infer dates, nationality, group size, destination or budget. Budget "
        "is total USD. Dates must be YYYY-MM-DD.\n\n"
        f"Current brief:\n{current.model_dump_json()}\n\nUser message:\n{message}"
    )


@dataclass
class ModelExtractor:
    """Extraction through the routed chat model, with the local parser behind it."""

    invoke: Callable[[str], _ModelPatch]

    def extract(self, message: str, current: TripBrief) -> BriefPatch:
        result = self.invoke(_extraction_prompt(message, current))
        fields = {
            k: v
            for k, v in result.model_dump().items()
            if v is not None and k not in ("startDate", "endDate")
        }
        if result.startDate and result.endDate:
            fields["dates"] = (result.startDate, result.endDate)
        return BriefPatch(**fields)


def create_model_extractor() -> BriefExtractor | None:
    model = create_routed_chat_model("brief-extraction")
    if model is None:
        return None
    structured = model.with_structured_output(_ModelPatch, method="function_calling")

    def invoke(prompt: str) -> _ModelPatch:
        result = structured.invoke(prompt)
        if result is None:
            raise ValueError("Extraction model returned no structured result.")
        return result if isinstance(result, _ModelPatch) else _ModelPatch.model_validate(result)

    return ModelExtractor(invoke)


def _extract_patch(
    message: str, current: TripBrief, extractor: BriefExtractor | None
) -> BriefPatch:
    selected = extractor or create_model_extractor()
    if selected is not None:
        try:
            return selected.extract(message, current)
        except (ValidationError, ValueError, RuntimeError) as error:
            print(f"[chat] Model brief extraction failed; using the local parser: {error}")
    return extract_brief_patch_locally(message)


def changed_fields(before: TripBrief, after: TripBrief) -> list[str]:
    return [f for f in PATCH_FIELDS if getattr(before, f) != getattr(after, f)]


def fallback_reply_for(plan: TripPlan) -> str:
    """A reply built from the plan itself, used when no model is configured."""
    parts = [s.summary.strip() for s in plan.sections if s.summary.strip()][:2]
    pending = next((h for h in plan.hitl if h.status == "pending"), None)
    if pending:
        parts.append(pending.detail)
    return " ".join(parts) or f"USD {plan.estTotal:.2f}"


def reply_prompt(
    message: str, before: TripBrief, after: TripBrief, fields: list[str], plan: TripPlan
) -> str:
    context = {
        "brief": after.model_dump(),
        "previousBrief": before.model_dump(),
        "changedFields": fields,
        "round": plan.round,
        "estimatedTotal": plan.estTotal,
        "budgetTotal": plan.budgetTotal,
        "overrunPct": plan.overrunPct,
        "sections": [
            {
                "label": s.label,
                "status": s.status,
                "summary": s.summary,
                "estimatedCost": s.estCost,
            }
            for s in plan.sections
        ],
        "pendingHumanDecisions": [
            {"title": h.title, "detail": h.detail} for h in plan.hitl if h.status == "pending"
        ],
    }
    return (
        "You are the trip coordinator speaking directly to a traveller. Write one warm, natural "
        "reply to the traveller's latest message after reviewing the updated trip plan.\n\n"
        "Rules:\n"
        "- Detect the language of the traveller's latest message and reply in that same language. "
        "Do not default to English, translate unnecessarily, or mix languages.\n"
        "- Acknowledge what they asked for before giving the result.\n"
        "- Be concise but personable (2-4 short sentences); sound like a thoughtful travel "
        "partner, not a status template.\n"
        "- Mention only facts supported by the plan context below. Never invent bookings, prices, "
        "availability or certainty.\n"
        "- If something still needs their decision, explain the most important next choice in "
        "plain language.\n"
        "- Do not mention prompts, models, agents, orchestration, rounds or implementation "
        "details.\n"
        "- Do not use a fixed formula such as 'Updated: ...'. Vary the wording naturally.\n\n"
        f"Traveller's latest message:\n{message}\n\n"
        f"Plan context (JSON):\n{json.dumps(context, ensure_ascii=False)}\n\n"
        "Return only the reply text."
    )


def _create_reply_generator() -> Callable[[str], str] | None:
    model = create_routed_chat_model("reply")
    if model is None:
        return None

    def generate(prompt: str) -> str:
        response = model.invoke(prompt)
        content = response.content
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            text = "".join(
                part if isinstance(part, str) else str(part.get("text", "")) for part in content
            ).strip()
            if text:
                return text
        raise ValueError("Reply model returned no text.")

    return generate


class ChatStream:
    """One conversational turn as progress events, and the response it produced.

    Iterate for progress, then read `response`, which is None until the iterator
    is exhausted. The reply is written after the plan, on the consumer's thread,
    so a consumer that stops early never records a turn it did not finish.
    """

    def __init__(
        self,
        plan_stream: PlanStream,
        message: str,
        before: TripBrief,
        after: TripBrief,
        mem: Any,
        trip_id: str,
        reply_generator: Callable[[str], str] | None,
    ) -> None:
        self._plan_stream = plan_stream
        self._message = message
        self._before = before
        self._after = after
        self._mem = mem
        self._trip_id = trip_id
        self._reply_generator = reply_generator
        self.response: ChatResponse | None = None

    def __iter__(self) -> Iterator[ProgressEvent]:
        yield from self._plan_stream

        plan = self._plan_stream.plan
        if plan is None:
            raise RuntimeError("The planning run finished without a plan to reply from.")

        reply = fallback_reply_for(plan)
        generate = self._reply_generator or _create_reply_generator()
        if generate is not None:
            try:
                reply = generate(
                    reply_prompt(
                        self._message,
                        self._before,
                        self._after,
                        changed_fields(self._before, self._after),
                        plan,
                    )
                )
            except Exception as error:  # noqa: BLE001 - a reply failure must not lose the plan
                print(f"[chat] Natural-language reply failed; using a local fallback: {error}")

        self._mem.append_short_term(self._trip_id, ChatTurn(role="assistant", content=reply))
        self.response = ChatResponse(reply=reply, plan=plan)


def run_trip_chat_stream(
    request: ChatRequest,
    options: OrchestratorOptions | None = None,
    *,
    extractor: BriefExtractor | None = None,
    reply_generator: Callable[[str], str] | None = None,
) -> ChatStream:
    """Apply a message to the brief, re-plan, and answer -- reporting progress."""
    options = options or OrchestratorOptions()
    base = request.brief or DEMO_BRIEF
    current = TripBrief(**{**base.model_dump(), "tripId": request.tripId})

    patch = _extract_patch(request.message, current, extractor)
    brief = apply_brief_patch(current, patch, request.tripId)

    mem = options.mem or default_memory
    mem.append_short_term(request.tripId, ChatTurn(role="user", content=request.message))

    options.mem = mem
    return ChatStream(
        plan_stream=run_orchestrator_stream(brief, options),
        message=request.message,
        before=current,
        after=brief,
        mem=mem,
        trip_id=request.tripId,
        reply_generator=reply_generator,
    )


def run_trip_chat(
    request: ChatRequest,
    options: OrchestratorOptions | None = None,
    *,
    extractor: BriefExtractor | None = None,
    reply_generator: Callable[[str], str] | None = None,
) -> ChatResponse:
    """Apply a message to the brief, re-plan, and answer in the traveller's language.

    The convenience form: same work as `run_trip_chat_stream`, with progress
    discarded. A UI that wants to watch the specialists iterates the stream.
    """
    stream = run_trip_chat_stream(
        request, options, extractor=extractor, reply_generator=reply_generator
    )
    for _ in stream:
        pass
    response = stream.response
    if response is None:
        raise RuntimeError("The chat turn finished without a response.")
    return response
