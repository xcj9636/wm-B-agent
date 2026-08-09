"""Durable, user-owned AI chat powered by the current runtime route snapshot."""
from datetime import datetime, timezone
from typing import AsyncIterator, Dict, List, Literal, Optional
from uuid import UUID

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.database import AIChatMessage, AIChatSession
from app.services.ai_runtime import AIRuntimeService
from app.services.llm.contracts import LLMUseCase
from app.services.llm.instrumented import SessionInvocationAuditSink
from app.services.llm.service import LLMService


SYSTEM_PROMPT = """You are B-agent, an AI copilot for foreign-trade teams. Help with
market selection, prospect research, multilingual outreach, reply handling,
quotation preparation and sales operations. Clearly separate facts from
assumptions, do not invent customer or compliance data, and ask for human
approval before any external message or irreversible business action."""


class AIChatMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    session_id: UUID
    role: Literal["user", "assistant"]
    content: str
    resolved_model: Optional[str] = None
    resolved_provider: Optional[str] = None
    usage: Dict[str, object] = Field(default_factory=dict)
    created_at: datetime


class AIChatSessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    title: str
    use_case: str
    created_at: datetime
    updated_at: datetime
    messages: List[AIChatMessageResponse] = Field(default_factory=list)


class AIChatService:
    MAX_HISTORY_MESSAGES = 30

    def __init__(self, db: Session, runtime: AIRuntimeService) -> None:
        self._db = db
        self._runtime = runtime

    def list_sessions(self, user_id: int) -> List[AIChatSessionResponse]:
        rows = (
            self._db.query(AIChatSession)
            .filter(AIChatSession.user_id == user_id)
            .order_by(AIChatSession.updated_at.desc())
            .all()
        )
        return [self._session_response(row, include_messages=False) for row in rows]

    def create_session(
        self, user_id: int, title: Optional[str] = None
    ) -> AIChatSessionResponse:
        row = AIChatSession(
            user_id=user_id,
            title=(title or "New conversation").strip()[:160]
            or "New conversation",
            use_case=LLMUseCase.LIVE_REPLY.value,
        )
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return self._session_response(row)

    def get_session(self, session_id: UUID, user_id: int) -> AIChatSessionResponse:
        return self._session_response(self._owned_session(session_id, user_id))

    def delete_session(self, session_id: UUID, user_id: int) -> None:
        row = self._owned_session(session_id, user_id)
        self._db.delete(row)
        self._db.commit()

    async def complete(
        self, session_id: UUID, user_id: int, content: str
    ) -> AIChatMessageResponse:
        session = self._owned_session(session_id, user_id)
        user_message = self._append_user_message(session, content)
        runtime_config = self._runtime.get_config()
        service = LLMService(
            self._runtime.build_backend(),
            audit_sink=SessionInvocationAuditSink(self._db),
            backend_name=runtime_config.backend,
        )
        backend = service._backend
        try:
            response = await service.complete(
                LLMUseCase.LIVE_REPLY,
                self._messages_for_model(session, user_message),
                temperature=0.3,
                max_output_tokens=1600,
                idempotency_key=f"ai-chat:{session.id}:{user_message.id}",
            )
        finally:
            close = getattr(backend, "aclose", None)
            if close is not None:
                await close()

        assistant = AIChatMessage(
            session_id=session.id,
            role="assistant",
            content=response.content,
            resolved_model=response.resolved_model,
            resolved_provider=response.resolved_provider,
            gateway_request_id=response.gateway_request_id,
            usage_json=response.usage.model_dump(),
        )
        self._finish_turn(session, assistant)
        return self._message_response(assistant)

    async def stream(
        self, session_id: UUID, user_id: int, content: str
    ) -> AsyncIterator[Dict[str, object]]:
        session = self._owned_session(session_id, user_id)
        user_message = self._append_user_message(session, content)
        runtime_config = self._runtime.get_config()
        service = LLMService(
            self._runtime.build_backend(),
            audit_sink=SessionInvocationAuditSink(self._db),
            backend_name=runtime_config.backend,
        )
        backend = service._backend
        fragments: List[str] = []
        metadata: Dict[str, object] = {}
        try:
            async for chunk in service.stream(
                LLMUseCase.LIVE_REPLY,
                self._messages_for_model(session, user_message),
                temperature=0.3,
                max_output_tokens=1600,
                idempotency_key=f"ai-chat:{session.id}:{user_message.id}",
            ):
                if chunk.delta:
                    fragments.append(chunk.delta)
                    yield {"event": "delta", "data": {"delta": chunk.delta}}
                if chunk.resolved_model:
                    metadata["resolved_model"] = chunk.resolved_model
                if chunk.resolved_provider:
                    metadata["resolved_provider"] = chunk.resolved_provider
                if chunk.gateway_request_id:
                    metadata["gateway_request_id"] = chunk.gateway_request_id
                if chunk.usage:
                    metadata["usage"] = chunk.usage.model_dump()
        finally:
            close = getattr(backend, "aclose", None)
            if close is not None:
                await close()

        assistant = AIChatMessage(
            session_id=session.id,
            role="assistant",
            content="".join(fragments),
            resolved_model=metadata.get("resolved_model"),
            resolved_provider=metadata.get("resolved_provider"),
            gateway_request_id=metadata.get("gateway_request_id"),
            usage_json=metadata.get("usage", {}),
        )
        self._finish_turn(session, assistant)
        yield {
            "event": "done",
            "data": self._message_response(assistant).model_dump(mode="json"),
        }

    def _owned_session(self, session_id: UUID, user_id: int) -> AIChatSession:
        row = (
            self._db.query(AIChatSession)
            .filter(
                AIChatSession.id == session_id,
                AIChatSession.user_id == user_id,
            )
            .first()
        )
        if row is None:
            raise KeyError("AI chat session not found")
        return row

    def _append_user_message(
        self, session: AIChatSession, content: str
    ) -> AIChatMessage:
        value = content.strip()
        if not value:
            raise ValueError("Message content cannot be empty")
        message = AIChatMessage(
            session_id=session.id,
            role="user",
            content=value,
            usage_json={},
        )
        self._db.add(message)
        self._db.flush()
        return message

    def _messages_for_model(
        self, session: AIChatSession, current: AIChatMessage
    ) -> List[Dict[str, str]]:
        history = [
            item
            for item in session.messages[-self.MAX_HISTORY_MESSAGES :]
            if item.id != current.id
        ]
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            *[
                {"role": item.role, "content": item.content}
                for item in history
                if item.role in {"user", "assistant"}
            ],
            {"role": "user", "content": current.content},
        ]

    def _finish_turn(
        self, session: AIChatSession, assistant: AIChatMessage
    ) -> None:
        session.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        self._db.add(assistant)
        self._db.commit()
        self._db.refresh(assistant)

    def _session_response(
        self, row: AIChatSession, *, include_messages: bool = True
    ) -> AIChatSessionResponse:
        return AIChatSessionResponse(
            id=row.id,
            title=row.title,
            use_case=row.use_case,
            created_at=self._utc(row.created_at),
            updated_at=self._utc(row.updated_at),
            messages=(
                [self._message_response(message) for message in row.messages]
                if include_messages
                else []
            ),
        )

    def _message_response(self, row: AIChatMessage) -> AIChatMessageResponse:
        return AIChatMessageResponse(
            id=row.id,
            session_id=row.session_id,
            role=row.role,
            content=row.content,
            resolved_model=row.resolved_model,
            resolved_provider=row.resolved_provider,
            usage=dict(row.usage_json or {}),
            created_at=self._utc(row.created_at),
        )

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def get_ai_chat_service(db: Session = Depends(get_db)) -> AIChatService:
    return AIChatService(db, AIRuntimeService(db))
