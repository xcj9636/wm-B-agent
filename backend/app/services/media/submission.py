"""Single safe boundary for policy-gated external media submission effects."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from app.integrations.fal_media import (
    MediaProviderError,
    MediaSubmissionReceipt,
)
from app.models.database import MediaGenerationJob
from app.services.agent_runtime.contracts import ExecutionPrincipal
from app.services.media.contracts import (
    GenerationIntent,
    GenerationMode,
    MediaPolicyDecision,
)
from app.services.media.jobs import (
    MediaGenerationJobService,
    MediaJobLeaseConflict,
)
from app.services.media.provider_inputs import (
    MediaProviderInputDenied,
    MediaProviderInputUnavailable,
)


class MediaIntentMismatch(RuntimeError):
    """Vault payload differs from the immutable job envelope."""


class MediaIntentVault(Protocol):
    def load(self, payload_ref: str) -> GenerationIntent: ...


class MediaPolicyVerifier(Protocol):
    def verify(
        self,
        decision: MediaPolicyDecision,
        intent: GenerationIntent,
        *,
        now: datetime,
    ) -> None: ...


class MediaSubmitAdapter(Protocol):
    async def submit(
        self,
        *,
        model_id: str,
        arguments: Dict[str, Any],
    ) -> MediaSubmissionReceipt: ...


class MediaProviderInputResolver(Protocol):
    def resolve(
        self,
        intent: GenerationIntent,
        *,
        now: datetime,
    ) -> Dict[str, Any]: ...


class PromptOnlyMediaProviderInputResolver:
    """Compatibility boundary that keeps non-T2V modes fail-closed."""

    def resolve(
        self,
        intent: GenerationIntent,
        *,
        now: datetime,
    ) -> Dict[str, Any]:
        del now
        if intent.mode == GenerationMode.TEXT_TO_VIDEO:
            return {"prompt": intent.prompt}
        raise MediaProviderInputDenied(
            "Media mode requires a server-resolved reference asset"
        )


class MediaSubmissionCoordinator:
    """Verify immutable intent immediately before beginning one external effect."""

    def __init__(
        self,
        db: Session,
        *,
        jobs: MediaGenerationJobService,
        vault: MediaIntentVault,
        policy: MediaPolicyVerifier,
        adapter: MediaSubmitAdapter,
        input_resolver: MediaProviderInputResolver | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._db = db
        self._jobs = jobs
        self._vault = vault
        self._policy = policy
        self._adapter = adapter
        self._input_resolver = (
            input_resolver or PromptOnlyMediaProviderInputResolver()
        )
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def submit_claimed(
        self,
        job_id: UUID,
        *,
        worker_id: str,
        fencing_token: int,
        principal: ExecutionPrincipal,
        decision: MediaPolicyDecision,
        now: datetime,
    ) -> MediaGenerationJob:
        checked_at = self._aware_utc(now)
        job = self._current_claim(job_id, worker_id, fencing_token, checked_at)
        generation_intent = self._vault.load(job.payload_ref)
        self._validate_envelope(job, generation_intent, principal)
        self._policy.verify(decision, generation_intent, now=checked_at)
        try:
            arguments = self._input_resolver.resolve(
                generation_intent,
                now=checked_at,
            )
        except MediaProviderInputDenied as exc:
            raise MediaIntentMismatch("media provider input was denied") from exc
        except MediaProviderInputUnavailable:
            raise

        self._jobs.begin_submission(
            job.id,
            worker_id=worker_id,
            fencing_token=fencing_token,
            now=checked_at,
        )
        try:
            receipt = await self._adapter.submit(
                model_id=job.model_id,
                arguments=arguments,
            )
        except MediaProviderError as exc:
            return self._jobs.mark_submission_unknown(
                job.id,
                worker_id=worker_id,
                fencing_token=fencing_token,
                error_code=exc.error_code,
                now=self._completed_at(),
            )
        except Exception:
            return self._jobs.mark_submission_unknown(
                job.id,
                worker_id=worker_id,
                fencing_token=fencing_token,
                error_code="provider_submission_failed",
                now=self._completed_at(),
            )
        return self._jobs.record_submitted(
            job.id,
            worker_id=worker_id,
            fencing_token=fencing_token,
            provider_request_id=receipt.request_id,
            now=self._completed_at(),
        )

    def _completed_at(self) -> datetime:
        """Read time after the network effect so stale leases fail closed."""
        return self._aware_utc(self._clock())

    def _current_claim(
        self,
        job_id: UUID,
        worker_id: str,
        fencing_token: int,
        now: datetime,
    ) -> MediaGenerationJob:
        naive_now = now.replace(tzinfo=None)
        job = self._db.get(MediaGenerationJob, job_id)
        if (
            job is None
            or job.status != "running"
            or job.effect_state != "none"
            or job.leased_by != worker_id
            or job.fencing_token != fencing_token
            or job.lease_until is None
            or job.lease_until <= naive_now
            or job.deadline_at <= naive_now
        ):
            raise MediaJobLeaseConflict("media job lease is no longer current")
        return job

    @staticmethod
    def _validate_envelope(
        job: MediaGenerationJob,
        intent: GenerationIntent,
        principal: ExecutionPrincipal,
    ) -> None:
        expected = (
            intent.input_hash() == job.intent_hash
            and intent.org_id == job.org_id == principal.org_id
            and intent.actor_user_id == job.owner_user_id == principal.user_id
            and intent.project_id == job.project_id
            and intent.shot_id == job.shot_id
            and intent.mode.value == job.mode
            and intent.sensitivity.value == job.sensitivity
        )
        if not expected:
            raise MediaIntentMismatch("media intent does not match the durable job")

    @staticmethod
    def _aware_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
