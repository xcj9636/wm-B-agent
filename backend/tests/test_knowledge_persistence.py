from uuid import uuid4

import pytest

from app.models.database import KnowledgeDocument
from app.services.agent_runtime.contracts import ExecutionPrincipal, Sensitivity
from app.services.knowledge import (
    KnowledgeIngestionService,
    KnowledgeRetrievalService,
    SQLKnowledgeACL,
    SQLKnowledgeSearchBackend,
)


def principal(org_id, *roles):
    return ExecutionPrincipal(
        org_id=org_id,
        user_id=31,
        roles=frozenset(roles or ("sales",)),
        entitlements_hash="b" * 64,
        authn_context="jwt:mfa",
    )


@pytest.mark.asyncio
async def test_persistent_knowledge_search_enforces_org_and_role_acl(db_session):
    org_id = uuid4()
    ingestion = KnowledgeIngestionService(db_session)
    document = ingestion.ingest(
        org_id=org_id,
        source_ref="catalog.pdf",
        title="Approved catalog",
        chunks=[
            "Verified MOQ for model AX-7 is 500 units.",
            "The approved packaging is recyclable cardboard.",
        ],
        authority="approved_document",
        sensitivity=Sensitivity.INTERNAL,
        allowed_roles={"sales"},
    )
    retrieval = KnowledgeRetrievalService(
        SQLKnowledgeSearchBackend(db_session),
        SQLKnowledgeACL(db_session),
    )

    allowed = await retrieval.retrieve(
        principal=principal(org_id, "sales"),
        query="AX-7 MOQ",
        sensitivity=Sensitivity.INTERNAL,
    )
    denied_role = await retrieval.retrieve(
        principal=principal(org_id, "finance"),
        query="AX-7 MOQ",
        sensitivity=Sensitivity.INTERNAL,
    )
    other_org = await retrieval.retrieve(
        principal=principal(uuid4(), "sales"),
        query="AX-7 MOQ",
        sensitivity=Sensitivity.INTERNAL,
    )

    assert len(allowed.evidence) == 1
    assert allowed.evidence[0].document_id == document.document_id
    assert allowed.evidence[0].document_version == 1
    assert allowed.evidence[0].source_ref == "catalog.pdf#chunk=1"
    assert denied_role.evidence == []
    assert other_org.evidence == []


@pytest.mark.asyncio
async def test_new_document_version_supersedes_old_chunks_and_acl(db_session):
    org_id = uuid4()
    ingestion = KnowledgeIngestionService(db_session)
    first = ingestion.ingest(
        org_id=org_id,
        source_ref="terms.pdf",
        title="Commercial terms",
        chunks=["Legacy MOQ is 100 units."],
        authority="approved_document",
        sensitivity=Sensitivity.INTERNAL,
        allowed_roles={"sales"},
    )
    second = ingestion.ingest(
        org_id=org_id,
        document_id=first.document_id,
        source_ref="terms.pdf",
        title="Commercial terms",
        chunks=["Current warranty period is 24 months."],
        authority="approved_document",
        sensitivity=Sensitivity.INTERNAL,
        allowed_roles={"sales"},
    )
    retrieval = KnowledgeRetrievalService(
        SQLKnowledgeSearchBackend(db_session),
        SQLKnowledgeACL(db_session),
    )

    old = await retrieval.retrieve(
        principal=principal(org_id),
        query="Legacy MOQ",
        sensitivity=Sensitivity.INTERNAL,
    )
    current = await retrieval.retrieve(
        principal=principal(org_id),
        query="warranty period",
        sensitivity=Sensitivity.INTERNAL,
    )

    assert second.document_version == 2
    assert old.evidence == []
    assert len(current.evidence) == 1
    assert current.evidence[0].document_version == 2
    rows = db_session.query(KnowledgeDocument).order_by(KnowledgeDocument.version).all()
    assert [row.status for row in rows] == ["superseded", "active"]


def test_identical_knowledge_ingestion_is_idempotent(db_session):
    org_id = uuid4()
    ingestion = KnowledgeIngestionService(db_session)
    values = {
        "org_id": org_id,
        "source_ref": "policy.pdf",
        "title": "Policy",
        "chunks": ["Only confirmed catalog facts may be quoted."],
        "authority": "approved_document",
        "sensitivity": Sensitivity.INTERNAL,
        "allowed_roles": {"sales"},
    }

    first = ingestion.ingest(**values)
    replay = ingestion.ingest(**values)

    assert replay.record_id == first.record_id
    assert replay.document_version == 1
    assert db_session.query(KnowledgeDocument).count() == 1


def test_ingestion_rejects_empty_chunks_or_acl(db_session):
    service = KnowledgeIngestionService(db_session)
    common = {
        "org_id": uuid4(),
        "source_ref": "empty.pdf",
        "title": "Empty",
        "authority": "approved_document",
        "sensitivity": Sensitivity.INTERNAL,
    }
    with pytest.raises(ValueError):
        service.ingest(chunks=[], allowed_roles={"sales"}, **common)
    with pytest.raises(ValueError):
        service.ingest(chunks=["content"], allowed_roles=set(), **common)
