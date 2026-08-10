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
from app.models.database import (
    AIChatMessage,
    AIChatSession,
    AgentRun,
    AgentRunEvent,
    AgentTurn,
)
from app.services.ai_runtime import AIRuntimeService
from app.services.agent_path_router import AgentExecutionProfile, AgentPathRouter
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
    ConcurrencyLimitExceeded,
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
from app.services.agent_run_events import (
    AgentRunEventResponse,
    AgentRunEventService,
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


class AIChatRunStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    turn_id: UUID
    session_id: UUID
    status: str


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
        row = (
            self._db.query(AIChatSession)
            .filter(
                AIChatSession.id == session_id,
                AIChatSession.user_id == user_id,
            )
            .with_for_update()
            .one_or_none()
        )
        if row is None:
            raise KeyError("AI chat session not found")
        runs = (
            self._db.query(AgentRun)
            .filter(
                AgentRun.session_id == session_id,
                AgentRun.user_id == user_id,
            )
            .with_for_update()
            .all()
        )
        now = datetime.utcnow()
        for run in runs:
            if run.status in {"queued", "running"}:
                run.status = "cancelled"
                run.error_code = "session_deleted"
                run.completed_at = now
                run.fencing_token += 1
                run.leased_by = None
                run.lease_until = None
                run.heartbeat_at = None
        run_ids = [run.id for run in runs]
        if run_ids:
            (
                self._db.query(AgentRunEvent)
                .filter(AgentRunEvent.run_id.in_(run_ids))
                .delete(synchronize_session=False)
            )
        self._db.delete(row)
        self._db.commit()

    def start_run(
        self,
        session_id: UUID,
        user_id: int,
        content: str,
        *,
        idempotency_key: str,
    ) -> tuple[AIChatRunStartResponse, bool]:
        """Atomically persist a chat turn and return its detachable run handle."""
        content = self._normalize_content(content)
        session = self._owned_session(session_id, user_id)
        classification = SensitiveDataClassifier().classify(
            content,
            intrinsic=Sensitivity.INTERNAL,
        )
        self._enforce_current_input_policy(classification.sensitivity)
        execution_profile = self._routing_profile(
            session,
            content=content,
            sensitivity=classification.sensitivity,
        )
        coordinator = AgentTurnCoordinator(self._db)
        try:
            turn, created = coordinator.start(
                session_id=session_id,
                user_id=user_id,
                idempotency_key=idempotency_key,
                input_hash=canonical_hash({"content": content}),
                commit=False,
            )
            if not created:
                run = (
                    self._db.query(AgentRun)
                    .filter(
                        AgentRun.turn_id == turn.id,
                        AgentRun.user_id == user_id,
                    )
                    .one_or_none()
                )
                if run is None:
                    raise IdempotencyConflict(
                        "Agent turn does not have a recoverable run"
                    )
                return self._run_start_response(run), False

            run, _ = AgentRunService(self._db).create(
                self._run_command(
                    turn=turn,
                    user_id=user_id,
                    content=content,
                    sensitivity=classification.sensitivity,
                    execution_profile=execution_profile,
                ),
                commit=False,
            )
            user_message = self._append_user_message(session, content)
            turn.user_message_id = user_message.id
            self._db.commit()
            self._db.refresh(run)
            return self._run_start_response(run), True
        except BaseException:
            self._db.rollback()
            raise

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
            execution_profile = self._routing_profile(
                session,
                content=content,
                sensitivity=classification.sensitivity,
            )
            run, _ = run_service.create(
                self._run_command(
                    turn=turn,
                    user_id=user_id,
                    content=content,
                    sensitivity=classification.sensitivity,
                    execution_profile=execution_profile,
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
                audit_sink=SessionInvocationAuditSink(
                    self._db,
                    run_id=run.id,
                    fencing_token=run.fencing_token,
                ),
                backend_name=runtime_config.backend,
            )
            (
                model_messages,
                redaction_vault,
                rehydration_placeholders,
            ) = self._redact_model_messages(
                self._messages_for_model(
                    session,
                    user_message,
                    history_limit=execution_profile.history_message_limit,
                ),
                run_id=str(turn.id),
            )
            response = await service.complete(
                LLMUseCase.LIVE_REPLY,
                model_messages,
                temperature=0.3,
                max_output_tokens=execution_profile.max_output_tokens,
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
            may_fail_turn = self._fail_run(
                run_service,
                run,
                worker_id=run_worker_id,
            )
            if may_fail_turn:
                coordinator.fail(
                    turn.id,
                    generation_epoch=turn.generation_epoch,
                )
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
            for event in self._replay_turn_events(turn, user_id=user_id):
                yield self._stream_item(event)
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
            execution_profile = self._routing_profile(
                session,
                content=content,
                sensitivity=classification.sensitivity,
            )
            run, _ = run_service.create(
                self._run_command(
                    turn=turn,
                    user_id=user_id,
                    content=content,
                    sensitivity=classification.sensitivity,
                    execution_profile=execution_profile,
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
            event_log = AgentRunEventService(self._db)
            started_event = event_log.append(
                run.id,
                worker_id=run_worker_id,
                fencing_token=run.fencing_token,
                event_type="run.started",
                data={
                    "run_id": str(run.id),
                    "turn_id": str(turn.id),
                },
                now=datetime.utcnow(),
            )
            yield self._stream_item(started_event)
            route_event = event_log.append(
                run.id,
                worker_id=run_worker_id,
                fencing_token=run.fencing_token,
                event_type="route.selected",
                data=execution_profile.model_dump(),
                now=datetime.utcnow(),
            )
            yield self._stream_item(route_event)
            runtime_config = self._runtime.get_config()
            backend = self._runtime.build_backend()
            service = LLMService(
                backend,
                audit_sink=SessionInvocationAuditSink(
                    self._db,
                    run_id=run.id,
                    fencing_token=run.fencing_token,
                ),
                backend_name=runtime_config.backend,
            )
            (
                model_messages,
                redaction_vault,
                rehydration_placeholders,
            ) = self._redact_model_messages(
                self._messages_for_model(
                    session,
                    user_message,
                    history_limit=execution_profile.history_message_limit,
                ),
                run_id=str(turn.id),
            )
            async for chunk in service.stream(
                LLMUseCase.LIVE_REPLY,
                model_messages,
                temperature=0.3,
                max_output_tokens=execution_profile.max_output_tokens,
                idempotency_key=f"ai-chat:{turn.id}:llm",
            ):
                if chunk.delta:
                    fragments.append(chunk.delta)
                    delta_event = event_log.append(
                        run.id,
                        worker_id=run_worker_id,
                        fencing_token=run.fencing_token,
                        event_type="message.delta",
                        data={"delta": chunk.delta},
                        now=datetime.utcnow(),
                    )
                    yield self._stream_item(delta_event)
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
            session.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            self._db.add(assistant)
            self._db.flush()
            turn.assistant_message_id = assistant.id
            completed_event = event_log.append(
                run.id,
                worker_id=run_worker_id,
                fencing_token=run.fencing_token,
                event_type="run.completed",
                data=self._message_response(assistant).model_dump(mode="json"),
                now=datetime.utcnow(),
                commit=False,
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
            self._db.commit()
            self._db.refresh(assistant)
        except BaseException:
            self._db.rollback()
            if run is not None and run.status == "running":
                try:
                    AgentRunEventService(self._db).append(
                        run.id,
                        worker_id=run_worker_id,
                        fencing_token=run.fencing_token,
                        event_type="run.failed",
                        data={"error_code": "agent_stream_failed"},
                        now=datetime.utcnow(),
                        commit=False,
                    )
                except RunLeaseConflict:
                    self._db.rollback()
            may_fail_turn = self._fail_run(
                run_service,
                run,
                worker_id=run_worker_id,
            )
            if may_fail_turn:
                coordinator.fail(
                    turn.id,
                    generation_epoch=turn.generation_epoch,
                )
            raise
        finally:
            if redaction_vault is not None:
                redaction_vault.purge(run_id=str(turn.id))
            if concurrency_lease is not None:
                await self._release_concurrency(concurrency_lease)
            if backend is not None:
                await self._close_backend(backend)
        yield self._stream_item(completed_event)

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
            execution_profile = AgentExecutionProfile.from_state(run.state_json)
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
                audit_sink=SessionInvocationAuditSink(
                    self._db,
                    run_id=run.id,
                    fencing_token=run.fencing_token,
                ),
                backend_name=runtime_config.backend,
            )
            (
                model_messages,
                redaction_vault,
                rehydration_placeholders,
            ) = self._redact_model_messages(
                self._messages_for_model(
                    session,
                    user_message,
                    history_limit=execution_profile.history_message_limit,
                ),
                run_id=str(turn.id),
            )
            event_log = AgentRunEventService(self._db)
            has_started = (
                self._db.query(AgentRunEvent)
                .filter(
                    AgentRunEvent.run_id == run.id,
                    AgentRunEvent.event_type == "run.started",
                )
                .first()
                is not None
            )
            if not has_started:
                event_log.append(
                    run.id,
                    worker_id=worker_id,
                    fencing_token=fencing_token,
                    event_type="run.started",
                    data={"run_id": str(run.id), "turn_id": str(turn.id)},
                    now=datetime.utcnow(),
                )
            has_route = (
                self._db.query(AgentRunEvent)
                .filter(
                    AgentRunEvent.run_id == run.id,
                    AgentRunEvent.event_type == "route.selected",
                )
                .first()
                is not None
            )
            if not has_route:
                event_log.append(
                    run.id,
                    worker_id=worker_id,
                    fencing_token=fencing_token,
                    event_type="route.selected",
                    data=execution_profile.model_dump(),
                    now=datetime.utcnow(),
                )
            event_log.append(
                run.id,
                worker_id=worker_id,
                fencing_token=fencing_token,
                event_type="stream.reset",
                data={"content": ""},
                now=datetime.utcnow(),
            )

            raw_fragments: List[str] = []
            pending_delta = ""
            metadata: Dict[str, object] = {}
            last_flush = datetime.now(timezone.utc)
            supports_stream = bool(
                getattr(
                    backend,
                    "supports_stream",
                    callable(getattr(backend, "stream", None)),
                )
            )
            if supports_stream:
                async for chunk in service.stream(
                    LLMUseCase.LIVE_REPLY,
                    model_messages,
                    temperature=0.3,
                    max_output_tokens=execution_profile.max_output_tokens,
                    idempotency_key=f"ai-chat:{turn.id}:llm",
                ):
                    if chunk.delta:
                        raw_fragments.append(chunk.delta)
                        pending_delta += chunk.delta
                    if chunk.resolved_model:
                        metadata["resolved_model"] = chunk.resolved_model
                    if chunk.resolved_provider:
                        metadata["resolved_provider"] = chunk.resolved_provider
                    if chunk.gateway_request_id:
                        metadata["gateway_request_id"] = chunk.gateway_request_id
                    if chunk.usage:
                        metadata["usage"] = chunk.usage.model_dump()
                    elapsed = (
                        datetime.now(timezone.utc) - last_flush
                    ).total_seconds()
                    if pending_delta and (
                        len(pending_delta.encode("utf-8")) >= 512
                        or elapsed >= 0.1
                    ):
                        event_log.append(
                            run.id,
                            worker_id=worker_id,
                            fencing_token=fencing_token,
                            event_type="message.delta",
                            data={"delta": pending_delta},
                            now=datetime.utcnow(),
                        )
                        pending_delta = ""
                        last_flush = datetime.now(timezone.utc)
            else:
                response = await service.complete(
                    LLMUseCase.LIVE_REPLY,
                    model_messages,
                    temperature=0.3,
                    max_output_tokens=execution_profile.max_output_tokens,
                    idempotency_key=f"ai-chat:{turn.id}:llm",
                )
                raw_fragments.append(response.content)
                pending_delta = response.content
                metadata = {
                    "resolved_model": response.resolved_model,
                    "resolved_provider": response.resolved_provider,
                    "gateway_request_id": response.gateway_request_id,
                    "usage": response.usage.model_dump(),
                }
            if pending_delta:
                event_log.append(
                    run.id,
                    worker_id=worker_id,
                    fencing_token=fencing_token,
                    event_type="message.delta",
                    data={"delta": pending_delta},
                    now=datetime.utcnow(),
                )
            response_content = redaction_vault.rehydrate(
                "".join(raw_fragments),
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
            session.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            self._db.add(assistant)
            self._db.flush()
            turn.assistant_message_id = assistant.id
            event_log.append(
                run.id,
                worker_id=worker_id,
                fencing_token=fencing_token,
                event_type="run.completed",
                data=self._message_response(assistant).model_dump(mode="json"),
                now=datetime.utcnow(),
                commit=False,
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
            self._db.commit()
            self._db.refresh(assistant)
            return self._message_response(assistant)
        except BaseException as exc:
            self._db.rollback()
            if lease_validated:
                if isinstance(
                    exc,
                    (ConcurrencyLimitExceeded, ConcurrencyUnavailable),
                ):
                    error_code = (
                        "agent_capacity_exhausted"
                        if isinstance(exc, ConcurrencyLimitExceeded)
                        else "agent_capacity_coordination_unavailable"
                    )
                    try:
                        run_service.requeue(
                            run.id,
                            worker_id=worker_id,
                            fencing_token=fencing_token,
                            now=datetime.utcnow(),
                            error_code=error_code,
                        )
                    except RunLeaseConflict:
                        run_service.recover_expired(now=datetime.utcnow())
                else:
                    if run is not None and run.status == "running":
                        try:
                            AgentRunEventService(self._db).append(
                                run.id,
                                worker_id=worker_id,
                                fencing_token=fencing_token,
                                event_type="run.failed",
                                data={"error_code": "agent_execution_failed"},
                                now=datetime.utcnow(),
                                commit=False,
                            )
                        except RunLeaseConflict:
                            self._db.rollback()
                    may_fail_turn = self._fail_run(
                        run_service,
                        run,
                        worker_id=worker_id,
                    )
                    if turn is not None and may_fail_turn:
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
        execution_profile: Optional[AgentExecutionProfile] = None,
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
            execution_profile=execution_profile,
        )

    @staticmethod
    def _routing_profile(
        session: AIChatSession,
        *,
        content: str,
        sensitivity: Sensitivity,
    ) -> AgentExecutionProfile:
        prior_message_count = sum(
            1
            for message in session.messages
            if message.role in {"user", "assistant"}
        )
        return AgentPathRouter(
            enabled=settings.AGENT_FAST_PATH_ENABLED,
            max_input_chars=settings.AGENT_FAST_PATH_MAX_INPUT_CHARS,
            max_history_messages=settings.AGENT_FAST_PATH_MAX_HISTORY_MESSAGES,
            fast_max_output_tokens=settings.AGENT_FAST_PATH_MAX_OUTPUT_TOKENS,
            deep_max_output_tokens=AIChatService.RESERVED_OUTPUT_TOKENS,
        ).route(
            content=content,
            sensitivity=sensitivity,
            prior_message_count=prior_message_count,
        )

    @staticmethod
    def _fail_run(
        service: AgentRunService,
        run: Optional[AgentRun],
        *,
        worker_id: str,
    ) -> bool:
        if run is None:
            return True
        if run.status != "running":
            return False
        try:
            service.fail(
                run.id,
                worker_id=worker_id,
                fencing_token=run.fencing_token,
                now=datetime.utcnow(),
                commit=False,
            )
            return True
        except RunLeaseConflict:
            service.recover_expired(now=datetime.utcnow())
            return False

    def _messages_for_model(
        self,
        session: AIChatSession,
        current: AIChatMessage,
        *,
        history_limit: Optional[int] = None,
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
        if history_limit is not None:
            eligible = eligible[-history_limit:]
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

    def _replay_turn_events(
        self,
        turn: AgentTurn,
        *,
        user_id: int,
    ) -> List[AgentRunEventResponse]:
        replay = self._replay_completed_turn(turn)
        run = (
            self._db.query(AgentRun)
            .filter(AgentRun.turn_id == turn.id, AgentRun.user_id == user_id)
            .one_or_none()
        )
        if run is not None:
            events = AgentRunEventService(self._db).list_for_user(
                run.id,
                user_id=user_id,
            )
            if events:
                return events
        # Compatibility for turns completed before durable stream events existed.
        now = datetime.now(timezone.utc)
        fallback_run_id = run.id if run is not None else uuid4()
        return [
            AgentRunEventResponse(
                run_id=fallback_run_id,
                sequence=1,
                event_type="message.delta",
                data={"delta": replay.content},
                occurred_at=now,
            ),
            AgentRunEventResponse(
                run_id=fallback_run_id,
                sequence=2,
                event_type="run.completed",
                data=replay.model_dump(mode="json"),
                occurred_at=now,
            ),
        ]

    @staticmethod
    def _stream_item(event: AgentRunEventResponse) -> Dict[str, object]:
        live_event_name = {
            "message.delta": "delta",
            "run.completed": "done",
        }.get(event.event_type, event.event_type)
        return {
            "id": event.sequence,
            "event": live_event_name,
            "data": event.data,
        }

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
    def _run_start_response(run: AgentRun) -> AIChatRunStartResponse:
        if run.session_id is None or run.turn_id is None:
            raise ValueError("AI chat run is missing its session or turn identity")
        return AIChatRunStartResponse(
            run_id=run.id,
            turn_id=run.turn_id,
            session_id=run.session_id,
            status=run.status,
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
