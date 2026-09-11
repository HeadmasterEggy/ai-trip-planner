"""Conversational entry point.

A message is turned into an explicit patch of the trip, the patch is merged into
whatever the conversation has collected so far, and -- once nothing required is
missing -- the orchestrator re-plans against it and a reply is written from the
resulting plan. While something is still missing the turn asks for it instead.

The extraction is deliberately conservative: only fields the traveller actually
stated are changed. Inferring a date or a budget the user did not give is worse
than asking, because the plan then silently drifts from what they asked for.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from .contracts import (
    BRIEF_FIELDS,
    FIELD_NAMES,
    BriefPatch,
    ChatRequest,
    ChatResponse,
    ChatTurn,
    ProgressEvent,
    TripPlan,
    brief_from_draft,
    draft_problem,
    merge_draft,
    missing_fields,
)
from .memory import memory as default_memory
from .models import create_routed_chat_model
from .workflow import OrchestratorOptions, PlanStream, run_orchestrator_stream

logger = logging.getLogger(__name__)


class BriefExtractor(Protocol):
    def extract(self, message: str, current: BriefPatch) -> BriefPatch: ...


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


# Words that open a sentence but never name a place. A short first message whose
# first word is one of these is conversation, not a destination -- including the
# vague ones, so that "somewhere warm" is read as a mood rather than a city.
_CONVERSATIONAL = {
    "hello",
    "hi",
    "hey",
    "hiya",
    "yo",
    "anyone",
    "help",
    "start",
    "test",
    "thanks",
    "thank",
    "somewhere",
    "anywhere",
    "wherever",
    "surprise",
    "maybe",
    "idk",
    "dunno",
    "please",
    "cheaper",
    "not",
    "no",
    "你好",
    "您好",
    "嗨",
    "在吗",
    "帮助",
    "测试",
}
_OPENING_MAX_CHARS = 48
_OPENING_MAX_WORDS = 3


def _opening_destination(message: str) -> str | None:
    """Read a bare opening message as the place the traveller means.

    "Tokyo" is the most natural first thing to say, and it matches none of the
    extraction patterns above -- so without this the assistant answers the
    traveller's only message with "where would you like to go?", the one reply
    that is certainly wrong. The rule is deliberately narrow: it is only
    consulted when nothing was extracted and the draft is still empty, and the
    message has to be a short noun phrase rather than a sentence or a greeting.

    A configured model reads the same message properly; this exists so the
    first turn works with no API key at all.
    """
    if "?" in message or len(message) > _OPENING_MAX_CHARS:
        return None
    candidate = _clean_destination(message).strip(" !！?？。.，,、~～")
    if not candidate:
        return None
    words = candidate.split()
    if len(words) > _OPENING_MAX_WORDS or words[0].casefold() in _CONVERSATIONAL:
        return None
    return candidate


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


def _extraction_prompt(message: str, draft: BriefPatch) -> str:
    return (
        "Extract only explicit updates to the trip. Use null for every field the user did "
        "not specify. Do not infer dates, nationality, group size, destination or budget. Budget "
        "is total USD. Dates must be YYYY-MM-DD. A field that is already known does not have to "
        "be repeated, and the known values may be empty at the start of a conversation.\n\n"
        f"Known so far:\n{draft.model_dump_json()}\n\nUser message:\n{message}"
    )


@dataclass
class ModelExtractor:
    """Extraction through the routed chat model, with the local parser behind it."""

    invoke: Callable[[str], _ModelPatch]

    def extract(self, message: str, current: BriefPatch) -> BriefPatch:
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


def _extract_patch(message: str, draft: BriefPatch, extractor: BriefExtractor | None) -> BriefPatch:
    selected = extractor or create_model_extractor()
    if selected is not None:
        try:
            return selected.extract(message, draft)
        except (ValidationError, ValueError, RuntimeError) as error:
            logger.warning("Model brief extraction failed; using the local parser: %s", error)
    return extract_brief_patch_locally(message)


def changed_fields(before: BriefPatch, after: BriefPatch) -> list[str]:
    return [f for f in BRIEF_FIELDS if getattr(before, f) != getattr(after, f)]


# The offline question, one line per required field. The short names used to
# mention the fields still to come come from the shared `FIELD_NAMES`, so a chat
# question and a form error ask for the same thing in the same words.
_ASK = {
    "destination": "Where would you like to go?",
    "dates": "When would you like to travel? Dates as YYYY-MM-DD, like 2026-10-01 to 2026-10-05.",
    "groupSize": "How many people are travelling?",
    "budgetTotal": "What is your total budget, in USD?",
}


def _and_list(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f" and {items[-1]}"


def fallback_question_for(missing: list[str]) -> str:
    """Ask for the next missing detail, without a model.

    Only the first missing field is asked for: reading a form out loud is worse
    than a conversation. The rest are named so the traveller knows what is still
    coming, which is also what makes the offline demo usable end to end.
    """
    question = _ASK[missing[0]]
    rest = [FIELD_NAMES[field] for field in missing[1:]]
    if not rest:
        return question
    return f"{question} I'll also need {_and_list(rest)}."


def fallback_reply_for(plan: TripPlan) -> str:
    """A reply built from the plan itself, used when no model is configured."""
    parts = [s.summary.strip() for s in plan.sections if s.summary.strip()][:2]
    pending = next((h for h in plan.hitl if h.status == "pending"), None)
    if pending:
        parts.append(pending.detail)
    return " ".join(parts) or f"USD {plan.estTotal:.2f}"


def reply_prompt(
    message: str, before: BriefPatch, after: BriefPatch, fields: list[str], plan: TripPlan
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


def question_prompt(message: str, draft: BriefPatch, missing: list[str]) -> str:
    """The prompt for a turn that is not ready to plan.

    Kept separate from `reply_prompt` because the job is the opposite one: there
    is no plan to describe, and the failure mode to guard against is a model
    inventing the very detail it was supposed to ask for.
    """
    known = {k: v for k, v in draft.model_dump().items() if v is not None}
    return (
        "You are the trip coordinator speaking directly to a traveller. You cannot plan yet -- "
        "some details are still missing -- so your whole job this turn is to ask for them.\n\n"
        "Rules:\n"
        "- Detect the language of the traveller's latest message and reply in that same language. "
        "Do not default to English, translate unnecessarily, or mix languages.\n"
        "- Acknowledge what they just told you, in your own words, before asking.\n"
        "- Ask for the missing details listed below, in that order, and keep it to 1-3 short "
        "sentences. Do not ask for anything else.\n"
        "- Never guess, assume or state a value for a missing detail as though the traveller had "
        "given it. Budget is total USD and dates are YYYY-MM-DD.\n"
        "- Do not mention prompts, models, agents, orchestration or implementation details.\n\n"
        f"Traveller's latest message:\n{message}\n\n"
        f"Already known (JSON):\n{json.dumps(known, ensure_ascii=False)}\n\n"
        f"Still missing, in the order worth asking: {', '.join(missing)}\n\n"
        "Return only the reply text."
    )


def _create_text_generator() -> Callable[[str], str] | None:
    """The routed model behind both the reply and the follow-up question.

    One role, because they are the same job seen twice: traveller-facing prose
    written from a context the caller already assembled.
    """
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

    A turn that had nothing to plan is the same type with an empty event stream:
    the caller drains it, gets a question back, and keeps the draft it was given.
    The alternative -- two response types -- would leave every caller guessing
    which one it is holding.
    """

    @property
    def interrupt(self) -> dict[str, Any] | None:
        """Set when the run paused for a human; the plan is already built."""
        return self._plan_stream.interrupt if self._plan_stream else None

    @property
    def thread_id(self) -> str | None:
        """The paused run's thread, needed to resume it. None when nothing ran."""
        return self._plan_stream.thread_id if self._plan_stream else None

    def __init__(
        self,
        *,
        draft: BriefPatch,
        message: str,
        before: BriefPatch,
        mem: Any,
        trip_id: str,
        plan_stream: PlanStream | None = None,
        missing: list[str] | None = None,
        reply_generator: Callable[[str], str] | None = None,
        question_generator: Callable[[str], str] | None = None,
    ) -> None:
        self._draft = draft
        self._message = message
        self._before = before
        self._mem = mem
        self._trip_id = trip_id
        self._plan_stream = plan_stream
        self._missing = list(missing or ())
        self._reply_generator = reply_generator
        self._question_generator = question_generator
        self.response: ChatResponse | None = None

    def __iter__(self) -> Iterator[ProgressEvent]:
        if self._plan_stream is not None:
            yield from self._plan_stream
        plan = self._plan_stream.plan if self._plan_stream else None

        if plan is None and not self._missing:
            # The draft was complete, so a plan was required. This is an
            # implementation error, not something to explain to the traveller.
            raise RuntimeError("The planning run finished without a plan to reply from.")
        reply = self._ask() if plan is None else self._answer(plan)

        self._mem.append_short_term(self._trip_id, ChatTurn(role="assistant", content=reply))
        self.response = ChatResponse(reply=reply, plan=plan, draft=self._draft)

    def _ask(self) -> str:
        question = fallback_question_for(self._missing)
        generate = self._question_generator or _create_text_generator()
        if generate is None:
            return question
        try:
            return generate(question_prompt(self._message, self._draft, self._missing))
        except Exception as error:  # noqa: BLE001 - a failed question must still ask something
            logger.warning("Natural-language question failed; using a local fallback: %s", error)
            return question

    def _answer(self, plan: TripPlan) -> str:
        reply = fallback_reply_for(plan)
        generate = self._reply_generator or _create_text_generator()
        if generate is None:
            return reply
        try:
            return generate(
                reply_prompt(
                    self._message,
                    self._before,
                    self._draft,
                    changed_fields(self._before, self._draft),
                    plan,
                )
            )
        except Exception as error:  # noqa: BLE001 - a reply failure must not lose the plan
            logger.warning("Natural-language reply failed; using a local fallback: %s", error)
            return reply


def run_trip_chat_stream(
    request: ChatRequest,
    options: OrchestratorOptions | None = None,
    *,
    extractor: BriefExtractor | None = None,
    reply_generator: Callable[[str], str] | None = None,
    question_generator: Callable[[str], str] | None = None,
    resume: Any = None,
    thread_id: str | None = None,
) -> ChatStream:
    """Apply a message to the trip, re-plan, and answer -- reporting progress.

    A turn has two shapes. When what the conversation has collected is complete
    the orchestrator runs and the reply is written from the plan. When something
    required is still missing nothing is planned: the reply asks for it and the
    draft comes back so the caller can keep collecting.

    There is deliberately no demo trip behind an empty request. The first screen
    has to be able to say "nothing here yet"; a plan the traveller never asked
    for is the one thing it must not show them.
    """
    options = options or OrchestratorOptions()

    # A complete brief outranks a draft: a caller that already holds one (the
    # form, or a script) is not mid-conversation, and its identity travels with
    # it.
    before = BriefPatch.from_brief(request.brief) if request.brief else request.draft
    before = before or BriefPatch()
    user_id = request.brief.userId if request.brief else request.userId

    patch = _extract_patch(request.message, before, extractor)
    after = merge_draft(before, patch)
    if patch.destination is None and before.is_empty():
        # The opening line is usually just the place. Only consulted when the
        # draft is empty, so "cheaper" halfway through is not read as a city.
        opening = _opening_destination(request.message)
        if opening:
            after = merge_draft(after, BriefPatch(destination=opening))

    mem = options.mem or default_memory
    mem.append_short_term(request.tripId, ChatTurn(role="user", content=request.message))
    options.mem = mem

    # Feasibility first, then completeness: a bad date span is worth reporting as
    # soon as it is said, not after three more questions. Same check the form and
    # the orchestrator run, so all three refuse an impossible trip with one
    # message -- an end before its start, or more cities than the trip has nights.
    problem = draft_problem(after)
    if problem:
        raise ValueError(problem)

    missing = missing_fields(after)
    if missing:
        return ChatStream(
            draft=after,
            before=before,
            message=request.message,
            mem=mem,
            trip_id=request.tripId,
            missing=missing,
            question_generator=question_generator,
        )

    brief = brief_from_draft(after, trip_id=request.tripId, user_id=user_id)
    return ChatStream(
        draft=after,
        before=before,
        message=request.message,
        mem=mem,
        trip_id=request.tripId,
        plan_stream=run_orchestrator_stream(brief, options, resume=resume, thread_id=thread_id),
        reply_generator=reply_generator,
    )


def run_trip_chat(
    request: ChatRequest,
    options: OrchestratorOptions | None = None,
    *,
    extractor: BriefExtractor | None = None,
    reply_generator: Callable[[str], str] | None = None,
    question_generator: Callable[[str], str] | None = None,
) -> ChatResponse:
    """Apply a message to the trip and answer in the traveller's language.

    The convenience form: same work as `run_trip_chat_stream`, with progress
    discarded. A UI that wants to watch the specialists iterates the stream.
    """
    stream = run_trip_chat_stream(
        request,
        options,
        extractor=extractor,
        reply_generator=reply_generator,
        question_generator=question_generator,
    )
    for _ in stream:
        pass
    response = stream.response
    if response is None:
        raise RuntimeError("The chat turn finished without a response.")
    return response
