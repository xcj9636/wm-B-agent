"""Tenant-isolated knowledge retrieval with authoritative post-authorization."""

import json
from datetime import datetime, timezone
from hashlib import sha256
import re
from typing import Dict, List, Optional, Protocol, Set
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.models.database import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentGrant,
)
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


class KnowledgeIngestionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    record_id: UUID
    document_id: UUID
    document_version: int = Field(ge=1)
    acl_policy_version: str
    index_version: str
    content_hash: str


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


class KnowledgeIngestionService:
    """Atomically publish immutable document versions, chunks, and role ACLs."""

    def __init__(self, session: Session) -> None:
        self._db = session

    def ingest(
        self,
        *,
        org_id: UUID,
        source_ref: str,
        title: str,
        chunks: List[str],
        authority: str,
        sensitivity: Sensitivity,
        allowed_roles: Set[str],
        document_id: Optional[UUID] = None,
    ) -> KnowledgeIngestionResult:
        normalized_chunks = [chunk.strip() for chunk in chunks if chunk.strip()]
        normalized_roles = sorted(
            {role.strip().lower() for role in allowed_roles if role.strip()}
        )
        source_ref = source_ref.strip()
        title = title.strip()
        authority = authority.strip()
        if not normalized_chunks:
            raise ValueError("At least one non-empty knowledge chunk is required")
        if not normalized_roles:
            raise ValueError("At least one knowledge role grant is required")
        if not source_ref or not title or not authority:
            raise ValueError("Knowledge source, title, and authority are required")

        if document_id is not None:
            foreign = (
                self._db.query(KnowledgeDocument)
                .filter(
                    KnowledgeDocument.document_id == document_id,
                    KnowledgeDocument.org_id != org_id,
                )
                .first()
            )
            if foreign is not None:
                raise PermissionError("Knowledge document belongs to another organization")

        manifest = {
            "source_ref": source_ref,
            "title": title,
            "chunks": normalized_chunks,
            "authority": authority,
            "sensitivity": sensitivity.value,
            "allowed_roles": normalized_roles,
        }
        content_hash = sha256(
            json.dumps(
                manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        latest_query = self._db.query(KnowledgeDocument).filter(
            KnowledgeDocument.org_id == org_id
        )
        if document_id is not None:
            latest_query = latest_query.filter(
                KnowledgeDocument.document_id == document_id
            )
        else:
            latest_query = latest_query.filter(
                KnowledgeDocument.source_ref == source_ref
            )
        latest = (
            latest_query.order_by(KnowledgeDocument.version.desc())
            .with_for_update()
            .first()
        )
        if latest is not None and latest.content_hash == content_hash:
            return self._result(latest)

        logical_id = document_id or (latest.document_id if latest else uuid4())
        version = (latest.version + 1) if latest else 1
        acl_policy_version = sha256(
            json.dumps(normalized_roles, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        index_version = sha256(
            f"{logical_id}:{version}:{content_hash}".encode("utf-8")
        ).hexdigest()
        row = KnowledgeDocument(
            document_id=logical_id,
            org_id=org_id,
            version=version,
            source_ref=source_ref,
            title=title,
            authority=authority,
            sensitivity=sensitivity.value,
            acl_policy_version=acl_policy_version,
            index_version=index_version,
            content_hash=content_hash,
            status="active",
        )
        try:
            if latest is not None:
                (
                    self._db.query(KnowledgeDocument)
                    .filter(
                        KnowledgeDocument.org_id == org_id,
                        KnowledgeDocument.document_id == logical_id,
                        KnowledgeDocument.status == "active",
                    )
                    .update({"status": "superseded"}, synchronize_session="fetch")
                )
            self._db.add(row)
            self._db.flush()
            for index, content in enumerate(normalized_chunks, start=1):
                self._db.add(
                    KnowledgeChunk(
                        document_record_id=row.record_id,
                        chunk_id=f"chunk-{index}",
                        content=content,
                        source_ref=f"{source_ref}#chunk={index}",
                        content_hash=sha256(content.encode("utf-8")).hexdigest(),
                    )
                )
            for role in normalized_roles:
                self._db.add(
                    KnowledgeDocumentGrant(
                        document_record_id=row.record_id,
                        principal_type="role",
                        principal_value=role,
                    )
                )
            self._db.commit()
            self._db.refresh(row)
        except Exception:
            self._db.rollback()
            raise
        return self._result(row)

    @staticmethod
    def _result(row: KnowledgeDocument) -> KnowledgeIngestionResult:
        return KnowledgeIngestionResult(
            record_id=row.record_id,
            document_id=row.document_id,
            document_version=row.version,
            acl_policy_version=row.acl_policy_version,
            index_version=row.index_version,
            content_hash=row.content_hash,
        )


class SQLKnowledgeSearchBackend:
    """Portable lexical baseline behind the isolated retrieval contract."""

    _TOKEN_PATTERN = re.compile(r"[\w-]+", re.UNICODE)

    def __init__(self, session: Session) -> None:
        self._db = session

    async def search(
        self,
        *,
        namespace: str,
        query: str,
        filters: Dict[str, object],
        limit: int,
    ) -> List[RawKnowledgeCandidate]:
        org_value = str(filters.get("org_id", ""))
        if namespace != f"org-{org_value}":
            return []
        try:
            org_id = UUID(org_value)
            max_sensitivity = Sensitivity(str(filters["max_sensitivity"]))
        except (KeyError, TypeError, ValueError):
            return []
        query_tokens = set(self._TOKEN_PATTERN.findall(query.lower()))
        if not query_tokens:
            return []

        rows = (
            self._db.query(KnowledgeChunk, KnowledgeDocument)
            .join(
                KnowledgeDocument,
                KnowledgeDocument.record_id == KnowledgeChunk.document_record_id,
            )
            .filter(
                KnowledgeDocument.org_id == org_id,
                KnowledgeDocument.status == "active",
            )
            .all()
        )
        candidates: List[RawKnowledgeCandidate] = []
        for chunk, document in rows:
            sensitivity = Sensitivity(document.sensitivity)
            if _SENSITIVITY_RANK[sensitivity] > _SENSITIVITY_RANK[max_sensitivity]:
                continue
            chunk_tokens = set(self._TOKEN_PATTERN.findall(chunk.content.lower()))
            overlap = len(query_tokens.intersection(chunk_tokens))
            if overlap == 0:
                continue
            score = overlap / len(query_tokens)
            if query.strip().lower() in chunk.content.lower():
                score += 0.25
            candidates.append(
                RawKnowledgeCandidate(
                    candidate_id=f"{document.record_id}:{chunk.chunk_id}",
                    org_id=document.org_id,
                    document_id=document.document_id,
                    document_version=document.version,
                    acl_policy_version=document.acl_policy_version,
                    index_version=document.index_version,
                    chunk_id=chunk.chunk_id,
                    content=chunk.content,
                    source_ref=chunk.source_ref,
                    authority=document.authority,
                    sensitivity=sensitivity,
                    valid_at=self._utc(document.created_at),
                    score=score,
                )
            )
        candidates.sort(key=lambda item: (-item.score, item.candidate_id))
        return candidates[: min(max(limit, 1), 20)]

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class SQLKnowledgeACL:
    """Authoritative, version-bound ACL check performed after search."""

    def __init__(self, session: Session) -> None:
        self._db = session

    def authorize(
        self,
        *,
        principal: ExecutionPrincipal,
        document_id: UUID,
        document_version: int,
        acl_policy_version: str,
    ) -> bool:
        document = (
            self._db.query(KnowledgeDocument)
            .filter(
                KnowledgeDocument.org_id == principal.org_id,
                KnowledgeDocument.document_id == document_id,
                KnowledgeDocument.version == document_version,
                KnowledgeDocument.acl_policy_version == acl_policy_version,
                KnowledgeDocument.status == "active",
            )
            .one_or_none()
        )
        if document is None:
            return False
        grants = (
            self._db.query(KnowledgeDocumentGrant)
            .filter(KnowledgeDocumentGrant.document_record_id == document.record_id)
            .all()
        )
        roles = {role.lower() for role in principal.roles}
        return any(
            (
                grant.principal_type == "role"
                and grant.principal_value in roles
            )
            or (
                grant.principal_type == "user"
                and grant.principal_value == str(principal.user_id)
            )
            for grant in grants
        )
