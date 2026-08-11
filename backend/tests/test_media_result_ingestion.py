from contextlib import asynccontextmanager
from datetime import date, datetime
from uuid import uuid4

import pytest
import httpx

from app.integrations.object_store import StoredObjectMetadata
from app.integrations.provider_media import SafeProviderMediaURLPolicy
from app.integrations.fal_media import MediaOutput
from app.models.database import MediaAsset, MediaGenerationJob
from app.services.media.result_ingestion import (
    HttpxMediaRemoteFetcher,
    MediaRemoteStream,
    MediaResultIngestionDenied,
    ProviderResultIngestor,
)


def generation_job(db_session):
    job = MediaGenerationJob(
        org_id=uuid4(),
        owner_user_id=7,
        project_id=uuid4(),
        storyboard_version_id=uuid4(),
        shot_id=uuid4(),
        runtime_revision_id=uuid4(),
        idempotency_key=f"result-ingestion:{uuid4()}",
        input_hash="a" * 64,
        intent_hash="b" * 64,
        payload_ref="vault://media-intents/result/ingestion",
        mode="text_to_video",
        provider="fal",
        model_id="fal-ai/veo3/fast",
        sensitivity="internal",
        status="submitted",
        effect_state="confirmed",
        provider_request_id="fal-request-1",
        reserved_cost_microusd=2_000_000,
        estimate_hash="c" * 64,
        budget_period_start=date(2026, 8, 1),
        deadline_at=datetime(2026, 8, 11, 14, 0, 0),
    )
    db_session.add(job)
    db_session.commit()
    return job


async def chunks(*values):
    for value in values:
        yield value


class FakeFetcher:
    def __init__(
        self,
        *,
        status_code=200,
        content_type="video/mp4",
        content_length=8,
        peer_ip="93.184.216.34",
        values=(b"video", b"123"),
    ):
        self.response = MediaRemoteStream(
            status_code=status_code,
            content_type=content_type,
            content_length=content_length,
            peer_ip=peer_ip,
            chunks=chunks(*values),
        )
        self.calls = 0

    @asynccontextmanager
    async def stream(self, approved):
        self.calls += 1
        yield self.response


class FakeObjectStore:
    backend_name = "s3"

    def __init__(self):
        self.calls = []

    def put_quarantined_generated(
        self,
        *,
        key,
        path,
        content_type,
        sha256,
        size_bytes,
    ):
        self.calls.append(
            {
                "key": key,
                "path": path,
                "content_type": content_type,
                "sha256": sha256,
                "size_bytes": size_bytes,
                "mode": path.stat().st_mode & 0o777,
                "payload": path.read_bytes(),
            }
        )
        return StoredObjectMetadata(
            key=key,
            size_bytes=size_bytes,
            content_type=content_type,
            sha256=sha256,
        )


def policy(resolver=lambda _host: ["93.184.216.34"]):
    return SafeProviderMediaURLPolicy(
        allowed_hosts={"fal.media", "*.fal.media"},
        resolver=resolver,
    )


@pytest.mark.asyncio
async def test_result_streams_to_private_quarantine_and_creates_secret_free_asset(
    db_session,
):
    job = generation_job(db_session)
    fetcher = FakeFetcher()
    store = FakeObjectStore()
    ingestor = ProviderResultIngestor(
        db_session,
        fetcher=fetcher,
        object_store=store,
        url_policy=policy(),
        max_bytes=1024,
    )
    output = MediaOutput(
        url="https://v3.fal.media/files/generated.mp4",
        content_type="video/mp4",
    )

    receipt = await ingestor.ingest(job=job, outputs=[output])
    replay = await ingestor.ingest(job=job, outputs=[output])

    assert receipt == replay
    assert receipt.result_ref.startswith("quarantine://media-assets/")
    assert fetcher.calls == 1
    assert len(store.calls) == 1
    assert store.calls[0]["payload"] == b"video123"
    assert store.calls[0]["mode"] == 0o600
    assert store.calls[0]["key"].startswith(
        f"quarantine/generated/{job.org_id}/{job.id}/"
    )
    asset = db_session.query(MediaAsset).one()
    assert asset.source == "ai_generated"
    assert asset.kind == "video"
    assert asset.quarantined is True
    assert asset.scan_status == "pending"
    assert asset.rights_status == "unknown"
    assert asset.consent_required is True
    assert asset.consent_status == "unknown"
    assert asset.metadata_json == {
        "generation_job_id": str(job.id),
        "provider": "fal",
        "provider_request_id": "fal-request-1",
    }
    assert "fal.media" not in repr(asset.__dict__)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fetcher", "match"),
    [
        (FakeFetcher(status_code=302), "status"),
        (FakeFetcher(peer_ip="127.0.0.1"), "peer"),
        (FakeFetcher(content_type="text/html"), "content type"),
        (
            FakeFetcher(content_length=2048, values=(b"x",)),
            "size",
        ),
        (
            FakeFetcher(content_length=None, values=(b"x" * 1025,)),
            "size",
        ),
    ],
)
async def test_result_ingestion_fails_closed_for_unsafe_response(
    db_session,
    fetcher,
    match,
):
    job = generation_job(db_session)
    store = FakeObjectStore()
    ingestor = ProviderResultIngestor(
        db_session,
        fetcher=fetcher,
        object_store=store,
        url_policy=policy(),
        max_bytes=1024,
    )

    with pytest.raises(MediaResultIngestionDenied, match=match):
        await ingestor.ingest(
            job=job,
            outputs=[
                MediaOutput(
                    url="https://v3.fal.media/files/generated.mp4",
                    content_type="video/mp4",
                )
            ],
        )

    assert store.calls == []
    assert db_session.query(MediaAsset).count() == 0


@pytest.mark.asyncio
async def test_result_ingestion_rejects_multiple_outputs_in_v1(db_session):
    job = generation_job(db_session)
    ingestor = ProviderResultIngestor(
        db_session,
        fetcher=FakeFetcher(),
        object_store=FakeObjectStore(),
        url_policy=policy(),
        max_bytes=1024,
    )
    output = MediaOutput(
        url="https://v3.fal.media/files/generated.mp4",
        content_type="video/mp4",
    )

    with pytest.raises(MediaResultIngestionDenied, match="exactly one"):
        await ingestor.ingest(job=job, outputs=[output, output])


class FakeNetworkStream:
    def __init__(self, address):
        self.address = address

    def get_extra_info(self, name):
        return self.address if name == "server_addr" else None


@pytest.mark.asyncio
async def test_httpx_fetcher_exposes_actual_peer_and_bounded_headers():
    def handler(_request):
        return httpx.Response(
            200,
            content=b"video123",
            headers={
                "content-type": "video/mp4; charset=binary",
                "content-length": "8",
            },
            extensions={
                "network_stream": FakeNetworkStream(("93.184.216.34", 443))
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = HttpxMediaRemoteFetcher(http_client=client)
    approved = policy().validate(
        "https://v3.fal.media/files/generated.mp4"
    )
    try:
        async with fetcher.stream(approved) as response:
            payload = b"".join([chunk async for chunk in response.chunks])
    finally:
        await client.aclose()

    assert response.peer_ip == "93.184.216.34"
    assert response.content_type == "video/mp4"
    assert response.content_length == 8
    assert payload == b"video123"


@pytest.mark.asyncio
async def test_httpx_fetcher_rejects_missing_peer_and_invalid_length():
    responses = iter(
        [
            httpx.Response(200, content=b"x"),
            httpx.Response(
                200,
                content=b"x",
                headers={"content-length": "not-an-integer"},
                extensions={
                    "network_stream": FakeNetworkStream(
                        ("93.184.216.34", 443)
                    )
                },
            ),
        ]
    )
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: next(responses))
    )
    fetcher = HttpxMediaRemoteFetcher(http_client=client)
    approved = policy().validate(
        "https://v3.fal.media/files/generated.mp4"
    )
    try:
        with pytest.raises(MediaResultIngestionDenied, match="peer"):
            async with fetcher.stream(approved):
                pass
        with pytest.raises(MediaResultIngestionDenied, match="size"):
            async with fetcher.stream(approved):
                pass
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_ingestion_rejects_empty_or_changed_content_type(db_session):
    job = generation_job(db_session)
    output = MediaOutput(
        url="https://v3.fal.media/files/generated.mp4",
        content_type="video/mp4",
    )
    for fetcher, match in (
        (FakeFetcher(content_length=0, values=()), "empty"),
        (
            FakeFetcher(
                content_type="image/png",
                content_length=8,
            ),
            "changed",
        ),
    ):
        with pytest.raises(MediaResultIngestionDenied, match=match):
            await ProviderResultIngestor(
                db_session,
                fetcher=fetcher,
                object_store=FakeObjectStore(),
                url_policy=policy(),
                max_bytes=1024,
            ).ingest(job=job, outputs=[output])
