"""Database-backed chat turn sequencing and generation fencing."""

from datetime import datetime
from typing import Literal, Tuple
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.database import AIChatSession, AgentTurn
from app.services.idempotency import IdempotencyConflict, canonical_hash


class TurnBusy(RuntimeError):
    pass


class StaleTurn(RuntimeError):
    pass


class AgentTurnCoordinator:
    def __init__(self, session: Session) -> None:
        self._db = session

    def start(
        self,
        *,
        session_id: UUID,
        user_id: int,
        idempotency_key: str,
        input_hash: str | None = None,
        policy: Literal["queue", "cancel_previous"] = "queue",
    ) -> Tuple[AgentTurn, bool]:
        chat = (
            self._db.query(AIChatSession)
            .filter(
                AIChatSession.id == session_id,
                AIChatSession.user_id == user_id,
            )
            .with_for_update()
            .one_or_none()
        )
        if chat is None:
            raise LookupError("AI chat session not found")

        existing = (
            self._db.query(AgentTurn)
            .filter(
                AgentTurn.session_id == session_id,
                AgentTurn.idempotency_key == idempotency_key,
            )
            .one_or_none()
        )
        if existing is not None:
            if input_hash is not None and existing.input_hash != input_hash:
                raise IdempotencyConflict(
                    "Agent turn idempotency key was reused for different input"
                )
            return existing, False

        active = (
            self._db.query(AgentTurn)
            .filter(
                AgentTurn.session_id == session_id,
                AgentTurn.status == "running",
            )
            .one_or_none()
        )
        if active is not None:
            if policy == "queue":
                raise TurnBusy("Another generation is active for this session")
            active.status = "superseded"
            active.completed_at = datetime.utcnow()
            self._db.flush()

        sequence = (
            self._db.query(func.max(AgentTurn.sequence))
            .filter(AgentTurn.session_id == session_id)
            .scalar()
            or 0
        ) + 1
        chat.generation_epoch += 1
        turn = AgentTurn(
            session_id=session_id,
            sequence=sequence,
            generation_epoch=chat.generation_epoch,
            idempotency_key=idempotency_key,
            input_hash=input_hash or canonical_hash({}),
            status="running",
        )
        self._db.add(turn)
        self._db.commit()
        self._db.refresh(turn)
        return turn, True

    def complete(
        self,
        turn_id: UUID,
        *,
        generation_epoch: int,
        commit: bool = True,
    ) -> AgentTurn:
        turn = self._db.get(AgentTurn, turn_id)
        if turn is None:
            raise LookupError("Agent turn not found")
        self._db.refresh(turn.session)
        if (
            turn.status != "running"
            or turn.generation_epoch != generation_epoch
            or turn.session.generation_epoch != generation_epoch
        ):
            raise StaleTurn("Agent turn lost its generation fence")
        turn.status = "completed"
        turn.completed_at = datetime.utcnow()
        if commit:
            self._db.commit()
            self._db.refresh(turn)
        return turn

    def fail(self, turn_id: UUID, *, generation_epoch: int) -> AgentTurn:
        turn = self._db.get(AgentTurn, turn_id)
        if turn is None:
            raise LookupError("Agent turn not found")
        if turn.status == "running" and turn.generation_epoch == generation_epoch:
            turn.status = "failed"
            turn.completed_at = datetime.utcnow()
            self._db.commit()
            self._db.refresh(turn)
        return turn
