"""Tenant-isolated knowledge retrieval with authoritative post-authorization."""

import json
from datetime import datetime, timezone
from hashlib import sha256
from typing import Dict, List, Optional, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.services.agent_runtime.contracts import ExecutionPrincipal, Sensitivity


class RawKnowledgeCandidate(BaseModel):
    """Untrusted search-backend result; security metadata may be absent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str
    org_id: Optional[UUID] = None
    document_id: Optional[UUID] = None
    document_version: Optional[int] = None
    acl_policy_version: Optional[str] = None
    index_version: Optional[str] = None
    chunk_id: Optional[str] = None
    content: str
    source_ref: str
    authority: str
    sensitivity: Sensitivity
    valid_at: datetime
    score: float


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str
    org_id: UUID
    document_id: UUID
    document_version: int = Field(ge=1)
    acl_policy_version: str
    index_version: str
    chunk_id: str
    content: str
    source_ref: str
    authority: str
    sensitivity: Sensitivity
    valid_at: datetime
    score: float


class RetrievalResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: str
    principal_id: str
    entitlements_hash: str
    authorized_at: datetime
    namespace: str
    evidence: List[Evidence]


class KnowledgeSearchBackend(Protocol):
    async def search(
        self,
        *,
        namespace: str,
        query: str,
        filters: Dict[str, object],
        limit: int,
    ) -> List[RawKnowledgeCandidate]:
        ...


class KnowledgeACL(Protocol):
    def authorize(
        self,
        *,
        principal: ExecutionPrincipal,
        document_id: UUID,
        document_version: int,
        acl_policy_version: str,
    ) -> bool:
        ...


_SENSITIVITY_RANK = {
    Sensitivity.PUBLIC: 0,
    Sensitivity.INTERNAL: 1,
    Sensitivity.CONFIDENTIAL: 2,
    Sensitivity.RESTRICTED: 3,
}


class KnowledgeRetrievalService:
    def __init__(self, backend: KnowledgeSearchBackend, acl: KnowledgeACL) -> None:
        self._backend = backend
        self._acl = acl

    async def retrieve(
        self,
        *,
        principal: ExecutionPrincipal,
        query: str,
        sensitivity: Sensitivity,
        limit: int = 8,
    ) -> RetrievalResult:
        namespace = f"org-{principal.org_id}"
        filters: Dict[str, object] = {
            "org_id": str(principal.org_id),
            "acl_required": True,
            "max_sensitivity": sensitivity.value,
            "active_only": True,
        }
        candidates = await self._backend.search(
            namespace=namespace,
            query=query,
            filters=filters,
            limit=min(max(limit, 1), 20),
        )
        evidence: List[Evidence] = []
        for candidate in sorted(candidates, key=lambda item: -item.score):
            if candidate.org_id != principal.org_id:
                continue
            if _SENSITIVITY_RANK[candidate.sensitivity] > _SENSITIVITY_RANK[sensitivity]:
                continue
            if not all(
                (
                    candidate.document_id,
                    candidate.document_version,
                    candidate.acl_policy_version,
                    candidate.index_version,
                    candidate.chunk_id,
                )
            ):
                continue
            if not self._acl.authorize(
                principal=principal,
                document_id=candidate.document_id,
                document_version=candidate.document_version,
                acl_policy_version=candidate.acl_policy_version,
            ):
                continue
            evidence.append(
                Evidence(
                    evidence_id=candidate.candidate_id,
                    org_id=candidate.org_id,
                    document_id=candidate.document_id,
                    document_version=candidate.document_version,
                    acl_policy_version=candidate.acl_policy_version,
                    index_version=candidate.index_version,
                    chunk_id=candidate.chunk_id,
                    content=candidate.content,
                    source_ref=candidate.source_ref,
                    authority=candidate.authority,
                    sensitivity=candidate.sensitivity,
                    valid_at=candidate.valid_at,
                    score=candidate.score,
                )
            )
            if len(evidence) >= limit:
                break

        authorized_at = datetime.now(timezone.utc)
        manifest = {
            "principal_id": str(principal.user_id),
            "entitlements_hash": principal.entitlements_hash,
            "namespace": namespace,
            "evidence": [
                {
                    "evidence_id": item.evidence_id,
                    "document_id": str(item.document_id),
                    "document_version": item.document_version,
                    "acl_policy_version": item.acl_policy_version,
                    "index_version": item.index_version,
                }
                for item in evidence
            ],
        }
        snapshot_id = sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return RetrievalResult(
            snapshot_id=snapshot_id,
            principal_id=str(principal.user_id),
            entitlements_hash=principal.entitlements_hash,
            authorized_at=authorized_at,
            namespace=namespace,
            evidence=evidence,
        )


def validate_citations(result: RetrievalResult, evidence_ids: List[str]) -> None:
    allowed = {item.evidence_id for item in result.evidence}
    if not set(evidence_ids).issubset(allowed):
        raise ValueError("Citation references evidence outside the retrieval snapshot")
