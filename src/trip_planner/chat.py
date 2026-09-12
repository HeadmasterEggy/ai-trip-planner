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
from datetime import date
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from . import dates, money
from .contracts import (
    BRIEF_FIELDS,
    DISPLAY_ONLY_FIELDS,
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
    prompt_facts,
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
    # The amount as stated, and the currency *as the traveller wrote it* -- "¥",
    # "元", "US$" -- never a code the model chose. Asked for "¥30,000" it answered
    # "JPY", and the plan was then costed against a seventh of the budget: the
    # symbol is mapped here, by `money.code_for`, where the app's own reading of
    # "¥" is the one that counts.
    budgetTotal: float | None = None
    budgetCurrency: str | None = None
    nationality: str | None = None


def _amount(value: str) -> float | None:
    try:
        parsed = float(value.replace(",", ""))
    except ValueError:
        return None
    return parsed if parsed > 0 else None


# What a traveller writes before the figure, and the words that mark a figure as
# the budget when no currency symbol does.
_BUDGET_HINT = r"(?:budget|spend|预算(?:改成|调整为|是|为)?|花费|费用|花销)"
_AMOUNT = r"([\d][\d,]*(?:\.\d+)?)"


def _token_near(text: str, start: int, end: int) -> str | None:
    """The currency written next to an amount, before it or after it."""
    before = re.search(rf"({money.TOKEN_PATTERN})\s*$", text[:start], re.IGNORECASE)
    if before:
        return before.group(1)
    after = re.search(rf"^\s*({money.TOKEN_PATTERN})", text[end:], re.IGNORECASE)
    return after.group(1) if after else None


def _find_budget(message: str) -> tuple[float, str | None] | None:
    """The budget the message states, as `(amount, currency token)`.

    A number on its own is not a budget -- "2 people" is not a budget of two --
    so it takes either the word or a currency to make one. The token comes back
    as written; `money.code_for` is what knows that "元" is CNY.
    """
    hint = re.search(_BUDGET_HINT, message, re.IGNORECASE)
    if hint:
        window = message[hint.end() : hint.end() + 24]
        found = re.search(_AMOUNT, window)
        if found:
            amount = _amount(found.group(1))
            if amount:
                return amount, _token_near(window, found.start(1), found.end(1))
    # No keyword, so something has to say the number is money.
    for found in re.finditer(_AMOUNT, message):
        amount = _amount(found.group(1))
        if amount:
            token = _token_near(message, found.start(1), found.end(1))
            if token:
                return amount, token
    return None


def _currency_token(message: str, result: _ModelPatch) -> str | None:
    """The currency to trust: the message's own spelling before the model's.

    The two disagree in exactly one situation, and it is not a rare one: a symbol
    that names more than one currency. "¥" is in both the yuan and the yen, the
    model answered "JPY", and a ¥30,000 budget was planned against USD 192. The
    traveller's own characters are evidence; a code the model inferred is not.
    """
    found = _find_budget(message)
    return found[1] if found and found[1] else result.budgetCurrency


def _budget_fields(amount: float, token: str | None) -> dict[str, Any]:
    """What the traveller said, and what it is in the unit the plan costs in."""
    code = money.code_for(token) or "USD"
    if code not in money.RATES:
        code = "USD"
    return {
        "budgetTotal": money.to_usd(amount, code),
        "budgetCurrency": code,
        "budgetAsGiven": amount,
    }


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


def extract_brief_patch_locally(message: str, *, today: date | None = None) -> BriefPatch:
    """Parse explicit updates without a model.

    This is the fallback when no extraction model is configured and when one
    fails, so it has to handle the phrasings the UI actually suggests. It reads
    both English and Chinese because the reply generator answers in the
    traveller's language and the input follows suit.

    `today` is only for tests: it is what an unstated year is read against.
    """
    patch: dict[str, Any] = {}

    found = dates.date_range(message, today=today)
    if found:
        patch["dates"] = found

    budget = _find_budget(message)
    if budget:
        patch.update(_budget_fields(*budget))

    # `人(?!民币)`: "预算 5000 人民币" is a budget in yuan, not 5000 people.
    group = re.search(
        r"(\d+)\s*(?:people|persons?|travell?ers?|人(?!民币))", message, re.IGNORECASE
    )
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
        "not specify. Do not infer dates, nationality, group size, destination or budget. A "
        "field that is already known does not have to be repeated, and the known values may be "
        "empty at the start of a conversation.\n\n"
        'Dates: return startDate and endDate exactly as the traveller wrote them -- "10.9" '
        'stays "10.9", "2026-10-01" stays "2026-10-01" -- and null for both unless the '
        "message states both ends of a range. Never add a year the traveller did not write: an "
        "unstated year is the app's decision, not yours.\n"
        "Budget: the traveller may use any currency. Return budgetTotal as the amount they "
        'stated, and budgetCurrency as the currency **exactly as they wrote it** -- "¥", '
        '"元", "US$", "CNY" -- or null if they did not name one. Do not translate a '
        "symbol into a code.\n\n"
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
            if v is not None and k not in ("startDate", "endDate", "budgetCurrency")
        }
        if result.startDate and result.endDate:
            # The prompt asks for ISO and the model usually complies, but "10.9"
            # is not wrong either: the same reader normalises whatever comes back.
            found = dates.date_range(f"{result.startDate} - {result.endDate}")
            if found is None:
                raise ValueError(
                    f"Could not read the dates {result.startDate!r} to {result.endDate!r}."
                )
            fields["dates"] = found
        if "budgetTotal" in fields:
            fields.update(
                _budget_fields(fields.pop("budgetTotal"), _currency_token(message, result))
            )
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
    """Which fields the traveller moved, as the reply prompt names them.

    The display-only fields are left out: they move with the budget, and naming
    them would tell a model there is a second currency in play -- which is all it
    needs to start converting (see `contracts.prompt_facts`).
    """
    return [
        field
        for field in BRIEF_FIELDS
        if field not in DISPLAY_ONLY_FIELDS and getattr(before, field) != getattr(after, field)
    ]


# The offline question, one line per required field. The short names used to
# mention the fields still to come come from the shared `FIELD_NAMES`, so a chat
# question and a form error ask for the same thing in the same words.
_ASK = {
    "destination": "Where would you like to go?",
    # The examples are the point: a traveller who is told "YYYY-MM-DD" types it,
    # and a traveller who is told "any way you like" types 10.9-12.9, which the
    # reader now handles.
    "dates": (
        "When would you like to travel? Any way you like — '10.9 to 12.9', "
        "'Oct 9–12', or 2026-10-09 to 2026-10-12."
    ),
    "groupSize": "How many people are travelling?",
    "budgetTotal": "What is your total budget? Any currency is fine.",
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
    # The reply speaks the unit the plan is costed in, and is not told the figure
    # the traveller used. Handing it both invites it to convert -- and it does, at
    # a rate it invents: asked to acknowledge "¥3,000" it produced "USD 192" from
    # the yen rate, having also relabelled CNY as JPY. The traveller's own figure
    # is shown by the plan itself (`ui/render.budget_as_given`), which is
    # arithmetic rather than prose.
    context = {
        "brief": prompt_facts(after),
        "previousBrief": prompt_facts(before),
        "changedFields": fields,
        "round": plan.round,
        # Every figure below is USD, including the budget: one named in another
        # currency was converted at intake (see `money.py`). Naming the unit on
        # every key is what stops a model relabelling a USD section cost as the
        # traveller's currency -- which it did, enthusiastically, before.
        "costsCurrency": "USD",
        "estimatedTotalUsd": plan.estTotal,
        "budgetTotalUsd": plan.budgetTotal,
        "overrunPct": plan.overrunPct,
        "sections": [
            {
                "label": s.label,
                "status": s.status,
                "summary": s.summary,
                "estimatedCostUsd": s.estCost,
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
        "- Acknowledge what they asked for before giving the result, and then confirm the dates "
        "and the budget **from the plan context** -- `brief.dates` and `budgetTotalUsd` -- rather "
        'than working them out from the message again. The traveller wrote "10.9-12.9"; the '
        "plan holds the year it was read as, and quoting your own reading instead is how a reply "
        "ends up naming a different year from the plan it is describing.\n"
        "- Every figure in the plan is USD, including the budget. If the traveller named their "
        "budget in another currency, say only that it has been converted into USD -- never "
        "restate their figure, never convert between currencies, and never estimate a rate. "
        "The plan itself shows them the conversion.\n"
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
        "given it. Do not ask for a particular date format or currency: take dates and the "
        "budget however they are written.\n"
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
            written = generate(
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
        stated = (
            {(self._draft.budgetAsGiven, self._draft.budgetCurrency)}
            if self._draft.budgetAsGiven and self._draft.budgetCurrency
            else set()
        )
        invented = [claim for claim in money.foreign_claims(written) if claim not in stated]
        if invented:
            # Quoting the traveller's own figure is the acknowledgement the whole
            # feature exists for, and it is allowed. Anything else in a foreign
            # currency means the model has started converting, which it cannot be
            # trusted to do -- the plan shows the real conversion, computed
            # (`ui/render.budget_as_given`), and this prose is replaced.
            logger.warning(
                "Reply invented a currency conversion (%s); using a local fallback.", invented
            )
            return reply
        return written


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
