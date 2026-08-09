"""Durable, user-owned AI chat powered by the current runtime route snapshot."""
from datetime import datetime, timezone
import logging
from typing import AsyncIterator, Dict, List, Literal, Optional
from uuid import UUID, uuid4

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.database import AIChatMessage, AIChatSession, AgentTurn
from app.services.ai_runtime import AIRuntimeService
from app.services.agent_runtime.context import (
    ContextAssembler,
    ContextBudgetPolicy,
    ContextRole,
    ContextSection,
    ContextTrust,
    TiktokenCounter,
)
from app.services.agent_runtime.prompts import get_default_prompt_registry
from app.services.agent_runtime.turns import AgentTurnCoordinator
from app.services.llm.contracts import LLMUseCase
from app.services.llm.instrumented import SessionInvocationAuditSink
from app.services.llm.service import LLMService
from app.services.idempotency import IdempotencyConflict, canonical_hash


logger = logging.getLogger(__name__)


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
    MODEL_CONTEXT_TOKENS = 16384
    RESERVED_OUTPUT_TOKENS = 1600
    SAFETY_MARGIN_TOKENS = 512

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
        self,
        session_id: UUID,
        user_id: int,
        content: str,
        *,
        idempotency_key: Optional[str] = None,
    ) -> AIChatMessageResponse:
        session = self._owned_session(session_id, user_id)
        coordinator = AgentTurnCoordinator(self._db)
        turn, created = coordinator.start(
            session_id=session.id,
            user_id=user_id,
            idempotency_key=(
                idempotency_key or f"ai-chat:{session.id}:{uuid4()}"
            ),
            input_hash=canonical_hash({"content": content.strip()}),
        )
        if not created:
            return self._replay_completed_turn(turn)
        user_message = self._append_user_message(session, content)
        turn.user_message_id = user_message.id
        self._db.flush()
        runtime_config = self._runtime.get_config()
        service = LLMService(
            self._runtime.build_backend(),
            audit_sink=SessionInvocationAuditSink(self._db),
            backend_name=runtime_config.backend,
        )
        backend = service._backend
        try:
            try:
                response = await service.complete(
                    LLMUseCase.LIVE_REPLY,
                    self._messages_for_model(session, user_message),
                    temperature=0.3,
                    max_output_tokens=1600,
                    idempotency_key=f"ai-chat:{turn.id}:llm",
                )
            finally:
                await self._close_backend(backend)
        except Exception:
            coordinator.fail(turn.id, generation_epoch=turn.generation_epoch)
            raise

        assistant = AIChatMessage(
            session_id=session.id,
            role="assistant",
            content=response.content,
            resolved_model=response.resolved_model,
            resolved_provider=response.resolved_provider,
            gateway_request_id=response.gateway_request_id,
            usage_json=response.usage.model_dump(),
        )
        coordinator.complete(
            turn.id,
            generation_epoch=turn.generation_epoch,
            commit=False,
        )
        self._finish_turn(session, assistant, turn=turn)
        return self._message_response(assistant)

    async def stream(
        self,
        session_id: UUID,
        user_id: int,
        content: str,
        *,
        idempotency_key: Optional[str] = None,
    ) -> AsyncIterator[Dict[str, object]]:
        session = self._owned_session(session_id, user_id)
        coordinator = AgentTurnCoordinator(self._db)
        turn, created = coordinator.start(
            session_id=session.id,
            user_id=user_id,
            idempotency_key=(
                idempotency_key or f"ai-chat:{session.id}:{uuid4()}"
            ),
            input_hash=canonical_hash({"content": content.strip()}),
        )
        if not created:
            replay = self._replay_completed_turn(turn)
            yield {"event": "delta", "data": {"delta": replay.content}}
            yield {
                "event": "done",
                "data": replay.model_dump(mode="json"),
            }
            return
        user_message = self._append_user_message(session, content)
        turn.user_message_id = user_message.id
        self._db.flush()
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
            try:
                async for chunk in service.stream(
                    LLMUseCase.LIVE_REPLY,
                    self._messages_for_model(session, user_message),
                    temperature=0.3,
                    max_output_tokens=1600,
                    idempotency_key=f"ai-chat:{turn.id}:llm",
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
                await self._close_backend(backend)
        except BaseException:
            coordinator.fail(turn.id, generation_epoch=turn.generation_epoch)
            raise

        assistant = AIChatMessage(
            session_id=session.id,
            role="assistant",
            content="".join(fragments),
            resolved_model=metadata.get("resolved_model"),
            resolved_provider=metadata.get("resolved_provider"),
            gateway_request_id=metadata.get("gateway_request_id"),
            usage_json=metadata.get("usage", {}),
        )
        coordinator.complete(
            turn.id,
            generation_epoch=turn.generation_epoch,
            commit=False,
        )
        self._finish_turn(session, assistant, turn=turn)
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
            for item in session.messages
            if item.id != current.id
        ]
        prompt = get_default_prompt_registry().active("live_reply").render(
            locale="zh-CN",
            use_case=LLMUseCase.LIVE_REPLY.value,
        )
        eligible = [item for item in history if item.role in {"user", "assistant"}]
        sections = []
        total = max(len(eligible), 1)
        for index, item in enumerate(eligible):
            sections.append(
                ContextSection(
                    section_id=f"message:{item.id}",
                    source_type="chat_message",
                    source_id=str(item.id),
                    source_version=str(getattr(item, "created_at", "1")),
                    content=item.content,
                    priority=min(90, 40 + int(50 * (index + 1) / total)),
                    trust=ContextTrust.UNTRUSTED,
                    role=(
                        ContextRole.USER
                        if item.role == "user"
                        else ContextRole.ASSISTANT
                    ),
                    sensitivity="confidential",
                )
            )
        sections.append(
            ContextSection(
                section_id=f"message:{current.id}",
                source_type="chat_message",
                source_id=str(current.id),
                source_version=str(getattr(current, "created_at", "1")),
                content=current.content,
                priority=100,
                trust=ContextTrust.UNTRUSTED,
                role=ContextRole.USER,
                sensitivity="confidential",
            )
        )
        snapshot = ContextAssembler(
            TiktokenCounter("cl100k_base"),
            ContextBudgetPolicy(
                model_context_tokens=self.MODEL_CONTEXT_TOKENS,
                reserved_output_tokens=self.RESERVED_OUTPUT_TOKENS,
                safety_margin_tokens=self.SAFETY_MARGIN_TOKENS,
            ),
        ).assemble(system_messages=[prompt], sections=sections)
        return [
            {"role": message.role.value, "content": message.content}
            for message in snapshot.messages
        ]

    def _finish_turn(
        self,
        session: AIChatSession,
        assistant: AIChatMessage,
        *,
        turn: Optional[AgentTurn] = None,
    ) -> None:
        session.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        self._db.add(assistant)
        self._db.flush()
        if turn is not None:
            turn.assistant_message_id = assistant.id
        self._db.commit()
        self._db.refresh(assistant)

    def _replay_completed_turn(self, turn: AgentTurn) -> AIChatMessageResponse:
        if turn.status == "running":
            raise IdempotencyConflict("Agent turn is already in progress")
        if turn.status != "completed" or turn.assistant_message_id is None:
            raise IdempotencyConflict("Agent turn cannot be replayed")
        assistant = self._db.get(AIChatMessage, turn.assistant_message_id)
        if assistant is None:
            raise IdempotencyConflict("Agent turn response is unavailable")
        return self._message_response(assistant)

    @staticmethod
    async def _close_backend(backend) -> None:
        close = getattr(backend, "aclose", None)
        if close is None:
            return
        try:
            await close()
        except Exception as exc:
            logger.warning(
                "LLM backend close failed",
                extra={"error_type": type(exc).__name__},
            )

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
