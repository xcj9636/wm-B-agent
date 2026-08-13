from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.integrations.fal_media import MediaOutput, MediaProviderResult
from app.models.database import (
    MediaGenerationJob,
    MediaProviderUsageReceipt,
    MediaRuntimeRevision,
)
from app.services.idempotency import canonical_hash
from app.services.media.usage_receipts import (
    MediaUsagePricingService,
    MediaUsageReceiptConflict,
    MediaUsageReceiptService,
)


NOW = datetime(2026, 8, 13, 16, 0, tzinfo=timezone.utc)


def submitted_job(db_session):
    pricing = {
        "schema_version": "fal-pricing-v1",
        "provider": "fal",
        "currency": "USD",
        "models": {
            "fal-ai/veo3/fast": {
                "unit": "second",
                "unit_price_microusd": 400_000,
            }
        },
    }
    revision = MediaRuntimeRevision(
        org_id=uuid4(),
        revision=1,
        provider="fal",
        enabled_modes=["text_to_video"],
        model_aliases={"text_to_video": "fal-ai/veo3/fast"},
        capability_snapshot={
            "provider": "fal",
            "schema_version": "fixture-v1",
            "models": [],
        },
        capability_snapshot_hash="a" * 64,
        pricing_snapshot=pricing,
        pricing_snapshot_hash=canonical_hash(pricing),
        created_by_user_id=7,
    )
    db_session.add(revision)
    db_session.flush()
    job = MediaGenerationJob(
        org_id=revision.org_id,
        owner_user_id=7,
        project_id=uuid4(),
        storyboard_version_id=uuid4(),
        shot_id=uuid4(),
        runtime_revision_id=revision.id,
        idempotency_key=f"media-usage:{uuid4()}",
        input_hash="a" * 64,
        intent_hash="b" * 64,
        payload_ref="vault://media-intents/usage/test",
        mode="text_to_video",
        provider="fal",
        model_id="fal-ai/veo3/fast",
        sensitivity="internal",
        status="submitted",
        effect_state="confirmed",
        provider_request_id="fal-request-1",
        reserved_cost_microusd=2_500_000,
        estimate_hash="c" * 64,
        budget_period_start=date(2026, 8, 1),
        deadline_at=NOW.replace(tzinfo=None),
    )
    db_session.add(job)
    db_session.commit()
    return job


def provider_result(*, request_id="fal-request-1", units="8"):
    return MediaProviderResult(
        provider_request_id=request_id,
        model_id="fal-ai/veo3/fast",
        billable_units=Decimal(units),
        outputs=[MediaOutput(url="https://v3.fal.media/files/output.mp4")],
    )


def test_usage_receipt_is_exactly_bound_and_unpriced(db_session):
    job = submitted_job(db_session)

    result = MediaUsageReceiptService(db_session).record(
        job=job,
        provider_result=provider_result(units="8.25"),
        now=NOW,
    )

    receipt = db_session.query(MediaProviderUsageReceipt).one()
    assert result.created is True
    assert receipt.job_id == job.id
    assert receipt.runtime_revision_id == job.runtime_revision_id
    assert receipt.provider == "fal"
    assert receipt.provider_request_id == job.provider_request_id
    assert receipt.model_id == job.model_id
    assert receipt.billable_units == Decimal("8.250000000")
    assert receipt.pricing_status == "unpriced"
    assert receipt.unit_price_microusd is None
    assert receipt.cost_microusd is None
    assert len(receipt.receipt_hash) == 64


def test_usage_receipt_replay_is_idempotent_but_conflict_fails_closed(db_session):
    job = submitted_job(db_session)
    service = MediaUsageReceiptService(db_session)

    first = service.record(
        job=job,
        provider_result=provider_result(units="8"),
        now=NOW,
    )
    replay = service.record(
        job=job,
        provider_result=provider_result(units="8.000"),
        now=NOW,
    )

    assert first.receipt_id == replay.receipt_id
    assert replay.created is False
    with pytest.raises(MediaUsageReceiptConflict):
        service.record(
            job=job,
            provider_result=provider_result(units="9"),
            now=NOW,
        )
    assert db_session.query(MediaProviderUsageReceipt).count() == 1


def test_pinned_pricing_converts_usage_to_exact_cost_once(db_session):
    job = submitted_job(db_session)
    MediaUsageReceiptService(db_session).record(
        job=job,
        provider_result=provider_result(units="5.25"),
        now=NOW,
    )

    cost = MediaUsagePricingService(db_session).actual_cost_microusd(job)
    replay = MediaUsagePricingService(db_session).actual_cost_microusd(job)

    receipt = db_session.query(MediaProviderUsageReceipt).one()
    assert cost == 2_100_000
    assert replay == cost
    assert receipt.pricing_status == "priced"
    assert receipt.unit_price_microusd == 400_000
    assert receipt.cost_microusd == cost
    assert receipt.pricing_snapshot_hash is not None


@pytest.mark.parametrize("mutation", ["hash", "missing", "fraction", "ceiling"])
def test_pinned_pricing_fails_closed_for_untrusted_or_unsafe_amount(
    db_session,
    mutation,
):
    job = submitted_job(db_session)
    MediaUsageReceiptService(db_session).record(
        job=job,
        provider_result=provider_result(units="5"),
        now=NOW,
    )
    revision = db_session.get(MediaRuntimeRevision, job.runtime_revision_id)
    if mutation == "hash":
        revision.pricing_snapshot_hash = "0" * 64
    elif mutation == "missing":
        revision.pricing_snapshot["models"] = {}
    elif mutation == "fraction":
        revision.pricing_snapshot["models"][job.model_id][
            "unit_price_microusd"
        ] = 333_333
        receipt = db_session.query(MediaProviderUsageReceipt).one()
        receipt.billable_units = Decimal("0.000000001")
    else:
        revision.pricing_snapshot["models"][job.model_id][
            "unit_price_microusd"
        ] = 600_000
    db_session.commit()

    with pytest.raises(MediaUsageReceiptConflict):
        MediaUsagePricingService(db_session).actual_cost_microusd(job)


@pytest.mark.parametrize("mutation", ["request", "model", "provider", "status"])
def test_usage_receipt_rejects_unbound_job_or_result(db_session, mutation):
    job = submitted_job(db_session)
    result = provider_result()
    if mutation == "request":
        result = provider_result(request_id="other-request")
    elif mutation == "model":
        result = result.model_copy(update={"model_id": "fal-ai/other"})
    elif mutation == "provider":
        job.provider = "other"
    else:
        job.status = "succeeded"
    db_session.commit()

    with pytest.raises(MediaUsageReceiptConflict):
        MediaUsageReceiptService(db_session).record(
            job=job,
            provider_result=result,
            now=NOW,
        )

    assert db_session.query(MediaProviderUsageReceipt).count() == 0
