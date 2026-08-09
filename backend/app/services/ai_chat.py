"""Durable, user-owned AI chat powered by the current runtime route snapshot."""
from datetime import datetime, timedelta, timezone
import logging
from typing import AsyncIterator, Dict, List, Literal, Optional
from uuid import UUID, uuid4

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models.database import AIChatMessage, AIChatSession, AgentRun, AgentTurn
from app.services.ai_runtime import AIRuntimeService
from app.services.agent_runtime.contracts import Sensitivity
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
from app.services.agent_concurrency import (
    ConcurrencyLease,
    ConcurrencyRequest,
    ConcurrencyUnavailable,
    DistributedConcurrencyLimiter,
    get_agent_concurrency_limiter,
)
from app.services.agent_runs import (
    AgentRunCommand,
    AgentRunService,
    RunLeaseConflict,
)
from app.services.data_policy import (
    DataPolicyUnavailable,
    RedactionVault,
    SensitiveDataClassifier,
)
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
    RUN_LEASE_SECONDS = 300

    def __init__(
        self,
        db: Session,
        runtime: AIRuntimeService,
        *,
        concurrency: DistributedConcurrencyLimiter,
    ) -> None:
        self._db = db
        self._runtime = runtime
        self._concurrency = concurrency

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
        content = self._normalize_content(content)
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
        backend = None
        redaction_vault = None
        rehydration_placeholders = set()
        run_service = AgentRunService(self._db)
        run: Optional[AgentRun] = None
        run_worker_id = f"api-chat:{turn.id}"
        concurrency_lease: Optional[ConcurrencyLease] = None
        try:
            classification = SensitiveDataClassifier().classify(
                content,
                intrinsic=Sensitivity.INTERNAL,
            )
            run, _ = run_service.create(
                self._run_command(
                    turn=turn,
                    user_id=user_id,
                    content=content,
                    sensitivity=classification.sensitivity,
                )
            )
            run = run_service.claim_one(
                run.id,
                worker_id=run_worker_id,
                now=datetime.utcnow(),
                lease_seconds=self.RUN_LEASE_SECONDS,
            )
            self._enforce_current_input_policy(classification.sensitivity)
            concurrency_lease = await self._concurrency.acquire(
                ConcurrencyRequest(
                    org_id=run.org_id,
                    user_id=user_id,
                ),
                now=datetime.now(timezone.utc),
                lease_seconds=settings.AGENT_CONCURRENCY_LEASE_SECONDS,
            )
            user_message = self._append_user_message(session, content)
            turn.user_message_id = user_message.id
            self._db.commit()
            self._db.refresh(turn)
            self._db.refresh(user_message)
            runtime_config = self._runtime.get_config()
            backend = self._runtime.build_backend()
            service = LLMService(
                backend,
                audit_sink=SessionInvocationAuditSink(self._db),
                backend_name=runtime_config.backend,
            )
            (
                model_messages,
                redaction_vault,
                rehydration_placeholders,
            ) = self._redact_model_messages(
                self._messages_for_model(session, user_message),
                run_id=str(turn.id),
            )
            response = await service.complete(
                LLMUseCase.LIVE_REPLY,
                model_messages,
                temperature=0.3,
                max_output_tokens=1600,
                idempotency_key=f"ai-chat:{turn.id}:llm",
            )
            response_content = redaction_vault.rehydrate(
                response.content,
                run_id=str(turn.id),
                allowed_placeholders=rehydration_placeholders,
            )

            assistant = AIChatMessage(
                session_id=session.id,
                role="assistant",
                content=response_content,
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
            run_service.complete(
                run.id,
                worker_id=run_worker_id,
                fencing_token=run.fencing_token,
                now=datetime.utcnow(),
                commit=False,
            )
            self._finish_turn(session, assistant, turn=turn)
            return self._message_response(assistant)
        except BaseException:
            self._db.rollback()
            self._fail_run(
                run_service,
                run,
                worker_id=run_worker_id,
            )
            coordinator.fail(turn.id, generation_epoch=turn.generation_epoch)
            raise
        finally:
            if redaction_vault is not None:
                redaction_vault.purge(run_id=str(turn.id))
            if concurrency_lease is not None:
                await self._release_concurrency(concurrency_lease)
            if backend is not None:
                await self._close_backend(backend)

    async def stream(
        self,
        session_id: UUID,
        user_id: int,
        content: str,
        *,
        idempotency_key: Optional[str] = None,
    ) -> AsyncIterator[Dict[str, object]]:
        content = self._normalize_content(content)
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
        backend = None
        redaction_vault = None
        rehydration_placeholders = set()
        fragments: List[str] = []
        metadata: Dict[str, object] = {}
        run_service = AgentRunService(self._db)
        run: Optional[AgentRun] = None
        run_worker_id = f"api-chat:{turn.id}"
        concurrency_lease: Optional[ConcurrencyLease] = None
        try:
            classification = SensitiveDataClassifier().classify(
                content,
                intrinsic=Sensitivity.INTERNAL,
            )
            run, _ = run_service.create(
                self._run_command(
                    turn=turn,
                    user_id=user_id,
                    content=content,
                    sensitivity=classification.sensitivity,
                )
            )
            run = run_service.claim_one(
                run.id,
                worker_id=run_worker_id,
                now=datetime.utcnow(),
                lease_seconds=self.RUN_LEASE_SECONDS,
            )
            self._enforce_current_input_policy(classification.sensitivity)
            concurrency_lease = await self._concurrency.acquire(
                ConcurrencyRequest(
                    org_id=run.org_id,
                    user_id=user_id,
                ),
                now=datetime.now(timezone.utc),
                lease_seconds=settings.AGENT_CONCURRENCY_LEASE_SECONDS,
            )
            user_message = self._append_user_message(session, content)
            turn.user_message_id = user_message.id
            self._db.commit()
            self._db.refresh(turn)
            self._db.refresh(user_message)
            runtime_config = self._runtime.get_config()
            backend = self._runtime.build_backend()
            service = LLMService(
                backend,
                audit_sink=SessionInvocationAuditSink(self._db),
                backend_name=runtime_config.backend,
            )
            (
                model_messages,
                redaction_vault,
                rehydration_placeholders,
            ) = self._redact_model_messages(
                self._messages_for_model(session, user_message),
                run_id=str(turn.id),
            )
            async for chunk in service.stream(
                LLMUseCase.LIVE_REPLY,
                model_messages,
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

            response_content = redaction_vault.rehydrate(
                "".join(fragments),
                run_id=str(turn.id),
                allowed_placeholders=rehydration_placeholders,
            )
            assistant = AIChatMessage(
                session_id=session.id,
                role="assistant",
                content=response_content,
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
            run_service.complete(
                run.id,
                worker_id=run_worker_id,
                fencing_token=run.fencing_token,
                now=datetime.utcnow(),
                commit=False,
            )
            self._finish_turn(session, assistant, turn=turn)
        except BaseException:
            self._db.rollback()
            self._fail_run(
                run_service,
                run,
                worker_id=run_worker_id,
            )
            coordinator.fail(turn.id, generation_epoch=turn.generation_epoch)
            raise
        finally:
            if redaction_vault is not None:
                redaction_vault.purge(run_id=str(turn.id))
            if concurrency_lease is not None:
                await self._release_concurrency(concurrency_lease)
            if backend is not None:
                await self._close_backend(backend)
        yield {
            "event": "done",
            "data": self._message_response(assistant).model_dump(mode="json"),
        }

    async def resume_claimed(
        self,
        run_id: UUID,
        *,
        worker_id: str,
        fencing_token: int,
    ) -> AIChatMessageResponse:
        """Resume one safely leased chat run from its durable user message."""
        run_service = AgentRunService(self._db)
        coordinator = AgentTurnCoordinator(self._db)
        run = self._db.get(AgentRun, run_id)
        turn: Optional[AgentTurn] = None
        lease_validated = False
        backend = None
        redaction_vault = None
        rehydration_placeholders: set[str] = set()
        concurrency_lease: Optional[ConcurrencyLease] = None
        try:
            if run is None:
                raise LookupError("Agent run not found")
            run = run_service.heartbeat(
                run.id,
                worker_id=worker_id,
                fencing_token=fencing_token,
                now=datetime.utcnow(),
                lease_seconds=self.RUN_LEASE_SECONDS,
            )
            lease_validated = True
            turn, session, user_message = self._recoverable_chat_context(run)
            self._enforce_current_input_policy(Sensitivity(run.sensitivity))
            concurrency_lease = await self._concurrency.acquire(
                ConcurrencyRequest(
                    org_id=run.org_id,
                    user_id=run.user_id,
                ),
                now=datetime.now(timezone.utc),
                lease_seconds=settings.AGENT_CONCURRENCY_LEASE_SECONDS,
            )
            runtime_config = self._runtime.get_config()
            backend = self._runtime.build_backend()
            service = LLMService(
                backend,
                audit_sink=SessionInvocationAuditSink(self._db),
                backend_name=runtime_config.backend,
            )
            (
                model_messages,
                redaction_vault,
                rehydration_placeholders,
            ) = self._redact_model_messages(
                self._messages_for_model(session, user_message),
                run_id=str(turn.id),
            )
            response = await service.complete(
                LLMUseCase.LIVE_REPLY,
                model_messages,
                temperature=0.3,
                max_output_tokens=1600,
                idempotency_key=f"ai-chat:{turn.id}:llm",
            )
            response_content = redaction_vault.rehydrate(
                response.content,
                run_id=str(turn.id),
                allowed_placeholders=rehydration_placeholders,
            )
            assistant = AIChatMessage(
                session_id=session.id,
                role="assistant",
                content=response_content,
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
            run_service.complete(
                run.id,
                worker_id=worker_id,
                fencing_token=fencing_token,
                now=datetime.utcnow(),
                commit=False,
            )
            self._finish_turn(session, assistant, turn=turn)
            return self._message_response(assistant)
        except BaseException:
            self._db.rollback()
            if lease_validated:
                self._fail_run(
                    run_service,
                    run,
                    worker_id=worker_id,
                )
                if turn is not None:
                    coordinator.fail(
                        turn.id,
                        generation_epoch=turn.generation_epoch,
                    )
            raise
        finally:
            if redaction_vault is not None and turn is not None:
                redaction_vault.purge(run_id=str(turn.id))
            if concurrency_lease is not None:
                await self._release_concurrency(concurrency_lease)
            if backend is not None:
                await self._close_backend(backend)

    def _recoverable_chat_context(
        self,
        run: AgentRun,
    ) -> tuple[AgentTurn, AIChatSession, AIChatMessage]:
        if (
            run.use_case != LLMUseCase.LIVE_REPLY.value
            or run.org_id != settings.AGENT_ORG_ID
            or run.session_id is None
            or run.turn_id is None
        ):
            raise ValueError("Agent run is not a recoverable AI chat run")
        session = self._owned_session(run.session_id, run.user_id)
        turn = self._db.get(AgentTurn, run.turn_id)
        if (
            turn is None
            or turn.session_id != session.id
            or turn.status != "running"
            or turn.generation_epoch != run.generation_epoch
            or session.generation_epoch != run.generation_epoch
            or turn.user_message_id is None
        ):
            raise RunLeaseConflict("AI chat turn lost its recovery fence")
        user_message = self._db.get(AIChatMessage, turn.user_message_id)
        if (
            user_message is None
            or user_message.session_id != session.id
            or user_message.role != "user"
        ):
            raise LookupError("Durable AI chat input is unavailable")
        return turn, session, user_message

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
        value = self._normalize_content(content)
        message = AIChatMessage(
            session_id=session.id,
            role="user",
            content=value,
            usage_json={},
        )
        self._db.add(message)
        self._db.flush()
        return message

    @staticmethod
    def _enforce_current_input_policy(sensitivity: Sensitivity) -> None:
        if sensitivity == Sensitivity.RESTRICTED:
            raise DataPolicyUnavailable(
                "Restricted data cannot be sent to the configured model route"
            )

    @staticmethod
    def _redact_model_messages(
        messages: List[Dict[str, str]],
        *,
        run_id: str,
    ) -> tuple[List[Dict[str, str]], RedactionVault, set[str]]:
        classifier = SensitiveDataClassifier()
        classifications = [
            classifier.classify(
                message["content"],
                intrinsic=Sensitivity.INTERNAL,
            )
            for message in messages
        ]
        if any(
            result.sensitivity == Sensitivity.RESTRICTED
            for result in classifications
        ):
            raise DataPolicyUnavailable(
                "Restricted context cannot be sent to the configured model route"
            )
        vault = RedactionVault()
        redacted: List[Dict[str, str]] = []
        current_placeholders: set[str] = set()
        for index, message in enumerate(messages):
            result = vault.redact(message["content"], run_id=run_id)
            redacted.append({**message, "content": result.text})
            if index == len(messages) - 1:
                current_placeholders = set(result.placeholders)
        return redacted, vault, current_placeholders

    @staticmethod
    def _normalize_content(content: str) -> str:
        value = content.strip()
        if not value:
            raise ValueError("Message content cannot be empty")
        return value

    @staticmethod
    def _run_command(
        *,
        turn: AgentTurn,
        user_id: int,
        content: str,
        sensitivity: Sensitivity,
    ) -> AgentRunCommand:
        deadline_seconds = max(
            int(settings.OMNIROUTE_TIMEOUT_SECONDS * 2),
            120,
        )
        return AgentRunCommand(
            idempotency_key=f"agent-run:ai-chat:{turn.id}",
            org_id=settings.AGENT_ORG_ID,
            user_id=user_id,
            session_id=turn.session_id,
            turn_id=turn.id,
            use_case=LLMUseCase.LIVE_REPLY.value,
            input={"content": content},
            sensitivity=sensitivity,
            generation_epoch=turn.generation_epoch,
            deadline_at=datetime.utcnow() + timedelta(seconds=deadline_seconds),
        )

    @staticmethod
    def _fail_run(
        service: AgentRunService,
        run: Optional[AgentRun],
        *,
        worker_id: str,
    ) -> None:
        if run is None or run.status != "running":
            return
        try:
            service.fail(
                run.id,
                worker_id=worker_id,
                fencing_token=run.fencing_token,
                now=datetime.utcnow(),
                commit=False,
            )
        except RunLeaseConflict:
            service.recover_expired(now=datetime.utcnow())

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

    async def _release_concurrency(self, lease: ConcurrencyLease) -> None:
        try:
            await self._concurrency.release(lease)
        except ConcurrencyUnavailable as exc:
            logger.warning(
                "Agent concurrency lease release deferred to TTL",
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
    return AIChatService(
        db,
        AIRuntimeService(db),
        concurrency=get_agent_concurrency_limiter(),
    )
