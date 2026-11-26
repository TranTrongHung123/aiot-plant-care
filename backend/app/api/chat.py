import asyncio
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from app.api.routes import valid_token
from app.core.settings import settings
from app.db.models import ChatConversation, ChatMessage, ChatToolCall, now
from app.db.session import Session
from app.integrations import chat_llm
from app.integrations.chat_llm import LLMInfo, Mode, Plan, ProviderError
from app.integrations.mqtt import bridge
from app.modules.chat import execute_plan, read_snapshot
from app.modules.knowledge import references_previous


class ChatRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def sanitized_handler(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                raise HTTPException(422, "Tham số chat không hợp lệ.") from None

        return sanitized_handler


def authorize_chat(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))
    ],
):
    if not credentials or not valid_token(credentials.credentials):
        raise HTTPException(401, "Khóa truy cập không hợp lệ.")
    if not settings.chat_enabled:
        raise HTTPException(503, "Trợ lý đang tắt trong cấu hình backend.")


router = APIRouter(
    prefix="/api/v1/chat", dependencies=[Depends(authorize_chat)], route_class=ChatRoute
)


class ConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_mode: Mode | None = None


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message: str = Field(min_length=1, max_length=1200)
    mode: Mode | None = None
    request_id: UUID = Field(default_factory=uuid4)


class ConversationView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    default_mode: Mode
    created_at: datetime
    updated_at: datetime


class MessageView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    role: str
    content: str
    turn: int
    status: str
    details: dict
    created_at: datetime


_send_slots = asyncio.Semaphore(2)
_create_slot = asyncio.Lock()
_recent_sends: deque[float] = deque()
_sensitive = re.compile(
    r"(?im)(?:\bBearer\s+\S+|\bsk-[A-Za-z0-9_-]{12,}|"
    r"\b[\w-]*(?:token|api[_ -]?key|password)\s*[:=]\s*[^\s]+)"
)
MAX_TURNS = 100


@dataclass(frozen=True)
class RecentContext:
    questions: tuple[str, ...] = ()
    image_refs: tuple[str, ...] = ()
    knowledge_refs: tuple[str, ...] = ()


def _recent_refs(answers: list[dict], kind: str) -> tuple[str, ...]:
    for details in answers:
        refs = tuple(
            item["ref"]
            for item in (details.get("sources") or ())
            if isinstance(item, dict)
            and item.get("kind") == kind
            and isinstance(item.get("ref"), str)
            and item["ref"]
        )
        if refs:
            return refs
    return ()


def _sanitize(text: str) -> str:
    for secret in (
        settings.chat_cloud_api_key.get_secret_value().strip(),
        settings.app_token,
        settings.openweather_api_key,
        settings.postgres_password,
        settings.minio_app_password,
    ):
        if secret and secret in text:
            text = text.replace(secret, "[đã ẩn]")
    return _sensitive.sub("[đã ẩn]", text)


def _summary(conversation, user: ChatMessage, assistant: ChatMessage) -> dict:
    return {
        "conversation_id": conversation.id,
        "message_id": assistant.id,
        "answer": assistant.content,
        "status": assistant.status,
        **assistant.details,
        "user_message": MessageView.model_validate(user),
        "assistant_message": MessageView.model_validate(assistant),
        "conversation": ConversationView.model_validate(conversation),
    }


def _existing(db, conversation, request_id: str, question: str, mode: Mode | None):
    user = db.get(ChatMessage, request_id)
    if user is None:
        return None
    if (
        user.conversation_id != conversation.id
        or user.role != "user"
        or user.content != question
        or (mode is not None and user.details.get("requested_mode") != mode)
    ):
        raise HTTPException(409, "Mã gửi tin nhắn đã được dùng cho một yêu cầu khác.")
    assistant = db.scalar(
        select(ChatMessage).where(
            ChatMessage.conversation_id == conversation.id,
            ChatMessage.turn == user.turn,
            ChatMessage.role == "assistant",
        )
    )
    return _summary(conversation, user, assistant)


def _load_turn(conversation_id, request, question):
    with Session() as db:
        conversation = db.get(ChatConversation, conversation_id)
        if not conversation:
            raise HTTPException(404, "Không tìm thấy hội thoại.")
        mode = request.mode or conversation.default_mode
        previous = _existing(db, conversation, str(request.request_id), question, request.mode)
        if previous:
            return previous, mode, RecentContext(), conversation.turn_count
        if conversation.turn_count >= MAX_TURNS:
            raise HTTPException(409, "Hội thoại đã đạt giới hạn. Hãy tạo hội thoại mới.")
        recent = db.execute(
            select(ChatMessage.role, ChatMessage.content, ChatMessage.details)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.turn.desc(), ChatMessage.role)
            .limit(8)
        ).all()
        answers = [row.details for row in recent if row.role == "assistant"]
        context = RecentContext(
            questions=tuple(row.content for row in reversed(recent) if row.role == "user"),
            image_refs=_recent_refs(answers, "image"),
            knowledge_refs=_recent_refs(answers, "knowledge"),
        )
        return None, mode, context, conversation.turn_count


def _save_turn(
    conversation_id: str,
    request_id: str,
    question: str,
    turn_count: int,
    plan: Plan,
    llm: LLMInfo,
    context: RecentContext,
) -> dict:
    with Session.begin() as db:
        conversation = db.get(ChatConversation, conversation_id, with_for_update=True)
        if not conversation:
            raise HTTPException(404, "Không tìm thấy hội thoại.")
        previous = _existing(db, conversation, request_id, question, llm.requested_mode)
        if previous:
            return previous
        if conversation.turn_count != turn_count:
            raise HTTPException(409, "Hội thoại vừa có tin nhắn mới. Hãy gửi lại.")
        at = now()
        follow_up = references_previous(question)
        snapshot = read_snapshot(
            db,
            plan,
            at,
            mqtt_connected=bridge.connected,
            prior_images=context.image_refs if follow_up else (),
        )
        results = execute_plan(
            plan,
            snapshot,
            at,
            question=question,
            prior_refs=context.knowledge_refs if follow_up else (),
            prior_question=context.questions[-1] if follow_up and context.questions else "",
        )
        statuses = {result.status for result in results}
        status = next(
            value
            for value in ("read_only", "insufficient_data", "ok", "unsupported")
            if value in statuses
        )
        agents = list(dict.fromkeys(result.agent for result in results))
        sources = {
            (source.kind, source.ref): source.model_dump(mode="json")
            for result in results
            for source in result.sources
        }
        turn = turn_count + 1
        user = ChatMessage(
            id=request_id,
            conversation_id=conversation_id,
            turn=turn,
            role="user",
            content=question,
            status="ok",
            details={"requested_mode": llm.requested_mode},
        )
        assistant = ChatMessage(
            conversation_id=conversation_id,
            turn=turn,
            role="assistant",
            content="\n\n".join(result.answer for result in results),
            status=status,
            details={
                "agents_used": agents,
                "sources": list(sources.values()),
                "llm": llm.model_dump(),
                "irrigation_decision": next(
                    (item.decision for item in results if item.decision), None
                ),
            },
        )
        db.add_all([user, assistant])
        db.flush()
        for result in results:
            args = {}
            if result.intent == "weather":
                args["weather_minutes"] = plan.weather_minutes
            if result.intent == "pump":
                args.update(action=plan.pump_action, seconds=plan.pump_seconds)
            db.add(
                ChatToolCall(
                    message_id=assistant.id,
                    agent=result.agent,
                    tool=result.intent,
                    sanitized_args=args,
                    sanitized_result={
                        "sources": [source.model_dump(mode="json") for source in result.sources],
                        "decision": result.decision,
                    },
                    duration_ms=result.duration_ms,
                    status=result.status,
                )
            )
        conversation.turn_count = turn
        conversation.updated_at = at
        conversation.default_mode = llm.requested_mode
        if turn == 1:
            conversation.title = " ".join(question.split())[:90]
        return _summary(conversation, user, assistant)


def _create(mode: Mode) -> ConversationView:
    with Session.begin() as db:
        if db.scalar(select(func.count()).select_from(ChatConversation)) >= 1000:
            raise HTTPException(409, "Không gian đã đạt giới hạn hội thoại.")
        row = ChatConversation(default_mode=mode)
        db.add(row)
        db.flush()
        return ConversationView.model_validate(row)


@router.post("/conversations", status_code=201)
async def create_conversation(request: ConversationRequest) -> ConversationView:
    async with _create_slot:
        return await run_in_threadpool(_create, request.default_mode or settings.chat_default_mode)


@router.get("/conversations")
def conversations(
    limit: Annotated[int, Query(ge=1, le=50)] = 25,
    offset: Annotated[int, Query(ge=0, le=1000)] = 0,
):
    with Session() as db:
        rows = list(
            db.scalars(
                select(ChatConversation)
                .order_by(ChatConversation.updated_at.desc(), ChatConversation.id)
                .offset(offset)
                .limit(limit + 1)
            )
        )
        return {
            "items": [ConversationView.model_validate(row) for row in rows[:limit]],
            "next_offset": offset + limit if len(rows) > limit else None,
        }


@router.get("/conversations/{conversation_id}/messages")
def messages(
    conversation_id: UUID,
    before_turn: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=25)] = 15,
):
    with Session() as db:
        conversation = db.get(ChatConversation, str(conversation_id))
        if not conversation:
            raise HTTPException(404, "Không tìm thấy hội thoại.")
        before = before_turn or conversation.turn_count + 1
        rows = list(
            db.scalars(
                select(ChatMessage)
                .where(
                    ChatMessage.conversation_id == conversation.id,
                    ChatMessage.turn < before,
                )
                .order_by(ChatMessage.turn.desc(), ChatMessage.role)
                .limit((limit + 1) * 2)
            )
        )
        page = rows[: limit * 2]
        return {
            "items": [MessageView.model_validate(row) for row in reversed(page)],
            "next_before_turn": page[-1].turn if len(rows) > len(page) else None,
            "conversation": ConversationView.model_validate(conversation),
        }


@router.post("/conversations/{conversation_id}/messages")
async def send_message(conversation_id: UUID, request: MessageRequest):
    question = _sanitize(request.message)
    previous, mode, context, turn = await run_in_threadpool(
        _load_turn, str(conversation_id), request, question
    )
    if previous:
        return previous
    at = monotonic()
    while _recent_sends and at - _recent_sends[0] >= 60:
        _recent_sends.popleft()
    if len(_recent_sends) >= settings.chat_rate_limit_per_minute or _send_slots.locked():
        raise HTTPException(429, "Trợ lý đang bận. Hãy thử lại sau.", headers={"Retry-After": "5"})
    _recent_sends.append(at)
    async with _send_slots:
        try:
            plan, llm = await chat_llm.route(
                mode, question, list(context.questions), chat_llm.providers()
            )
        except ProviderError as exc:
            raise HTTPException(exc.status, str(exc)) from None
        return await run_in_threadpool(
            _save_turn,
            str(conversation_id),
            str(request.request_id),
            question,
            turn,
            plan,
            llm,
            context,
        )


@router.get("/providers")
def list_providers():
    return {
        "default_mode": settings.chat_default_mode,
        "auto_preference": settings.chat_auto_preference,
        "providers": [
            {"name": provider.name, "model": provider.model, "configured": provider.configured}
            for provider in chat_llm.providers().values()
        ],
    }


@router.get("/health")
async def chat_health():
    registry = chat_llm.providers()
    availability = await asyncio.gather(*(provider.healthy() for provider in registry.values()))
    return {
        "providers": dict(zip(registry, availability, strict=True)),
        "available": any(availability),
    }
