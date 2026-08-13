"""Bounded, policy-gated worker loop for irreversible media submissions."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from app.services.media.intent_vault import MediaIntentVaultUnavailable
from app.services.media.policy import MediaPolicyDenied
from app.services.media.provider_inputs import MediaProviderInputUnavailable
from app.services.media.submission_authorizer import (
    MediaSubmissionAuthorizationDenied,
)
from app.services.media.submission import MediaIntentMismatch
from app.services.media.worker_runtime import MediaRuntimeUnavailable


async def run_media_submission_batch(
    *,
    jobs,
    vault,
    authorizer,
    runtime_factory,
    coordinator_builder: Callable,
    worker_id: str,
    now: datetime,
    batch_size: int,
    lease_seconds: int,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, int]:
    """Submit one claimed job at a time without placing secrets on the queue."""
    if not worker_id or len(worker_id) > 100:
        raise ValueError("worker_id is invalid")
    if not 1 <= batch_size <= 100:
        raise ValueError("batch_size must be between 1 and 100")
    if not 300 <= lease_seconds <= 900:
        raise ValueError("lease_seconds must be between 300 and 900")
    counters = {
        "claimed": 0,
        "submitted": 0,
        "submission_unknown": 0,
        "failed_before_submission": 0,
        "deferred": 0,
    }
    for index in range(batch_size):
        checked_at = now if index == 0 else clock()
        claimed = jobs.claim_batch(
            worker_id=worker_id,
            now=checked_at,
            limit=1,
            lease_seconds=lease_seconds,
        )
        if not claimed:
            break
        job = claimed[0]
        counters["claimed"] += 1
        adapter = None
        try:
            intent = vault.load(job.payload_ref)
            authorization = authorizer.authorize(job, intent, now=checked_at)
            adapter = runtime_factory.build(job)
            effect_checked_at = clock()
            result = await coordinator_builder(adapter).submit_claimed(
                job.id,
                worker_id=worker_id,
                fencing_token=job.fencing_token,
                principal=authorization.principal,
                decision=authorization.decision,
                now=effect_checked_at,
            )
        except MediaIntentVaultUnavailable:
            _fail_before_submission(
                jobs,
                job,
                worker_id=worker_id,
                now=checked_at,
                error_code="media_intent_unavailable",
            )
            counters["failed_before_submission"] += 1
            continue
        except MediaSubmissionAuthorizationDenied:
            _fail_before_submission(
                jobs,
                job,
                worker_id=worker_id,
                now=checked_at,
                error_code="media_authorization_denied",
            )
            counters["failed_before_submission"] += 1
            continue
        except MediaPolicyDenied:
            _fail_before_submission(
                jobs,
                job,
                worker_id=worker_id,
                now=checked_at,
                error_code="media_policy_denied",
            )
            counters["failed_before_submission"] += 1
            continue
        except MediaIntentMismatch:
            _fail_before_submission(
                jobs,
                job,
                worker_id=worker_id,
                now=checked_at,
                error_code="media_intent_mismatch",
            )
            counters["failed_before_submission"] += 1
            continue
        except (MediaRuntimeUnavailable, MediaProviderInputUnavailable):
            counters["deferred"] += 1
            continue
        finally:
            if adapter is not None:
                await adapter.aclose()

        if result.status == "submitted":
            counters["submitted"] += 1
        elif result.status == "submission_unknown":
            counters["submission_unknown"] += 1
        else:
            raise RuntimeError("media submission returned an invalid state")
    return counters


def _fail_before_submission(
    jobs,
    job,
    *,
    worker_id: str,
    now: datetime,
    error_code: str,
) -> None:
    jobs.fail_before_submission(
        job.id,
        worker_id=worker_id,
        fencing_token=job.fencing_token,
        error_code=error_code,
        now=now,
    )
