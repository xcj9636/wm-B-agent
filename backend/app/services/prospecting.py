"""Evidence-backed prospect search persistence and customer import workflow."""
from datetime import datetime
import hashlib
import re
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    TypeAdapter,
    model_validator,
)
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.integrations.hunter import HunterClient, HunterConnectorError
from app.models.database import (
    ConnectorConfiguration,
    Customer,
    ProspectingContact,
    ProspectingSearch,
)


DEPARTMENTS = {
    "executive",
    "it",
    "finance",
    "management",
    "sales",
    "legal",
    "support",
    "hr",
    "marketing",
    "communication",
    "education",
    "design",
    "health",
    "operations",
}
SENIORITIES = {"junior", "senior", "executive"}
SEARCH_VERIFICATION_STATUSES = {"valid", "accept_all", "unknown"}
CONTACT_VERIFICATION_STATUSES = {
    "valid",
    "invalid",
    "accept_all",
    "webmail",
    "disposable",
    "unknown",
}
EMAIL_ADAPTER = TypeAdapter(EmailStr)


class EvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str
    uri: str
    extracted_on: Optional[str] = None
    last_seen_on: Optional[str] = None


class ProspectingContactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    company: Optional[str] = None
    domain: Optional[str] = None
    position: Optional[str] = None
    department: Optional[str] = None
    seniority: Optional[str] = None
    contact_type: Optional[str] = None
    confidence: Optional[int] = None
    decision_maker: Optional[bool] = None
    verification_status: str
    verification_date: Optional[str] = None
    evidence: List[EvidenceReference] = Field(default_factory=list)
    imported_customer_id: Optional[int] = None


class ProspectingSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    provider: str
    mode: str
    query: Dict[str, Any]
    status: str
    connector_version: int
    result_count: int
    error_code: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    contacts: List[ProspectingContactResponse] = Field(default_factory=list)


class ProspectingSearchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["domain_search", "email_finder"]
    domain: Optional[str] = Field(default=None, max_length=500)
    company: Optional[str] = Field(default=None, min_length=2, max_length=255)
    first_name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    last_name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    full_name: Optional[str] = Field(default=None, min_length=2, max_length=200)
    limit: int = Field(default=10, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=10000)
    contact_type: Optional[Literal["personal", "generic"]] = None
    seniorities: List[str] = Field(default_factory=list, max_length=3)
    departments: List[str] = Field(default_factory=list, max_length=15)
    decision_maker: Optional[bool] = None
    verification_statuses: List[str] = Field(
        default_factory=lambda: ["valid"],
        max_length=3,
    )
    max_duration: int = Field(default=10, ge=3, le=20)

    @model_validator(mode="after")
    def validate_search_contract(self) -> "ProspectingSearchCreate":
        if not self.domain and not self.company:
            raise ValueError("domain or company is required")
        if self.mode == "email_finder" and not self.full_name:
            if not (self.first_name and self.last_name):
                raise ValueError("a full name or first and last name is required")
        if not set(self.seniorities).issubset(SENIORITIES):
            raise ValueError("unsupported seniority")
        if not set(self.departments).issubset(DEPARTMENTS):
            raise ValueError("unsupported department")
        if not set(self.verification_statuses).issubset(
            SEARCH_VERIFICATION_STATUSES
        ):
            raise ValueError("unsupported verification status")
        return self


class ProspectingImportCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contact_ids: List[UUID] = Field(min_length=1, max_length=100)


class ProspectingImportResponse(BaseModel):
    created: int
    existing: int
    customer_ids: List[int]


class ProspectingProviderFailure(RuntimeError):
    def __init__(
        self,
        error_code: str,
        *,
        retryable: bool,
        legal_restriction: bool,
    ) -> None:
        self.error_code = error_code
        self.retryable = retryable
        self.legal_restriction = legal_restriction
        super().__init__(error_code)


class ProspectingRecordNotFound(LookupError):
    pass


class ProspectingService:
    def __init__(self, db: Session) -> None:
        self._db = db

    async def create_search(
        self,
        command: ProspectingSearchCreate,
        *,
        user_id: int,
        hunter: HunterClient,
    ) -> ProspectingSearchResponse:
        domain = self._normalize_domain(command.domain) if command.domain else None
        query = self._safe_query(command, domain=domain)
        row = ProspectingSearch(
            user_id=user_id,
            provider="hunter",
            mode=command.mode,
            query_json=query,
            status="running",
            connector_version=self._connector_version(),
        )
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)

        try:
            if command.mode == "domain_search":
                data = await hunter.domain_search(
                    domain=domain,
                    company=command.company,
                    limit=command.limit,
                    offset=command.offset,
                    contact_type=command.contact_type,
                    seniorities=command.seniorities,
                    departments=command.departments,
                    decision_maker=command.decision_maker,
                    verification_statuses=command.verification_statuses,
                )
                candidates = list(data.get("emails") or [])
                defaults = {
                    "company": data.get("organization") or command.company,
                    "domain": data.get("domain") or domain,
                }
            else:
                data = await hunter.email_finder(
                    domain=domain,
                    company=command.company,
                    first_name=command.first_name,
                    last_name=command.last_name,
                    full_name=command.full_name,
                    max_duration=command.max_duration,
                )
                candidates = [data] if data.get("email") else []
                defaults = {
                    "company": data.get("company") or command.company,
                    "domain": data.get("domain") or domain,
                }
            self._persist_candidates(row, candidates, defaults=defaults)
        except HunterConnectorError as exc:
            row.status = "failed"
            row.error_code = exc.error_code
            row.completed_at = datetime.utcnow()
            self._db.commit()
            raise ProspectingProviderFailure(
                exc.error_code,
                retryable=exc.retryable,
                legal_restriction=exc.legal_restriction,
            ) from exc

        row.status = "completed"
        row.result_count = len(row.contacts)
        row.completed_at = datetime.utcnow()
        self._db.commit()
        self._db.refresh(row)
        return self._response(row)

    def list_searches(
        self,
        *,
        user_id: int,
        limit: int = 20,
    ) -> List[ProspectingSearchResponse]:
        rows = (
            self._db.query(ProspectingSearch)
            .filter(ProspectingSearch.user_id == user_id)
            .order_by(ProspectingSearch.created_at.desc())
            .limit(limit)
            .all()
        )
        return [self._response(row) for row in rows]

    def get_search(
        self,
        search_id: UUID,
        *,
        user_id: int,
    ) -> ProspectingSearchResponse:
        row = (
            self._db.query(ProspectingSearch)
            .filter(
                ProspectingSearch.id == search_id,
                ProspectingSearch.user_id == user_id,
            )
            .one_or_none()
        )
        if row is None:
            raise ProspectingRecordNotFound("Prospecting search not found")
        return self._response(row)

    def import_contacts(
        self,
        command: ProspectingImportCommand,
        *,
        user_id: int,
    ) -> ProspectingImportResponse:
        requested_ids = list(dict.fromkeys(command.contact_ids))
        contacts = (
            self._db.query(ProspectingContact)
            .join(ProspectingSearch)
            .filter(
                ProspectingContact.id.in_(requested_ids),
                ProspectingSearch.user_id == user_id,
            )
            .all()
        )
        if len(contacts) != len(requested_ids):
            raise ProspectingRecordNotFound("Prospecting contact not found")

        created = 0
        existing = 0
        customer_ids: List[int] = []
        for contact in contacts:
            customer = (
                self._db.query(Customer)
                .filter(func.lower(Customer.email) == contact.email)
                .one_or_none()
            )
            if customer is None:
                customer = self._customer_from_contact(contact)
                self._db.add(customer)
                self._db.flush()
                created += 1
            else:
                existing += 1
            self._apply_icp_context(customer, contact)
            contact.imported_customer_id = customer.id
            customer_ids.append(customer.id)
        self._db.commit()
        return ProspectingImportResponse(
            created=created,
            existing=existing,
            customer_ids=customer_ids,
        )

    def _persist_candidates(
        self,
        search: ProspectingSearch,
        candidates: List[Dict[str, Any]],
        *,
        defaults: Dict[str, Any],
    ) -> None:
        seen = {
            email
            for (email,) in (
                self._db.query(ProspectingContact.email)
                .filter(ProspectingContact.search_id == search.id)
                .all()
            )
        }
        for candidate in candidates:
            raw_email = candidate.get("email") or candidate.get("value")
            email = self._normalize_email(raw_email)
            if email is None or email in seen:
                continue
            seen.add(email)
            verification = candidate.get("verification") or {}
            status = str(verification.get("status") or "unknown").lower()
            if status not in CONTACT_VERIFICATION_STATUSES:
                status = "unknown"
            self._db.add(
                ProspectingContact(
                    search_id=search.id,
                    email=email,
                    first_name=self._text(candidate.get("first_name"), 100),
                    last_name=self._text(candidate.get("last_name"), 100),
                    company=self._text(defaults.get("company"), 255),
                    domain=self._text(defaults.get("domain"), 255),
                    position=self._text(candidate.get("position"), 255),
                    department=self._text(candidate.get("department"), 50),
                    seniority=self._text(candidate.get("seniority"), 50),
                    contact_type=self._text(candidate.get("type"), 30),
                    confidence=self._confidence(
                        candidate.get("confidence", candidate.get("score"))
                    ),
                    decision_maker=candidate.get("decision_maker"),
                    verification_status=status,
                    verification_date=self._text(verification.get("date"), 20),
                    evidence_json=self._safe_evidence(candidate.get("sources")),
                )
            )
        self._db.flush()

    def _customer_from_contact(self, contact: ProspectingContact) -> Customer:
        suppressed = contact.verification_status != "valid"
        customer = Customer(
            username=self._customer_username(contact.email),
            platform="hunter",
            email=contact.email,
            website=(f"https://{contact.domain}" if contact.domain else None),
            company_name=contact.company,
            job_title=contact.position,
            status="new",
            tags_json=["hunter", "prospect"],
            contact_info={
                "first_name": contact.first_name,
                "last_name": contact.last_name,
                "department": contact.department,
                "seniority": contact.seniority,
                "decision_maker": contact.decision_maker,
            },
            source_data_json={
                "provider": "hunter",
                "prospecting_contact_id": str(contact.id),
                "search_id": str(contact.search_id),
                "verification_status": contact.verification_status,
                "verification_date": contact.verification_date,
                "evidence": list(contact.evidence_json or []),
            },
            custom_fields={
                "email_verification_status": contact.verification_status,
                "contact_suppressed": suppressed,
                **(
                    {"suppression_reason": f"email_{contact.verification_status}"}
                    if suppressed
                    else {}
                ),
            },
        )
        self._apply_icp_context(customer, contact)
        return customer

    @staticmethod
    def _apply_icp_context(
        customer: Customer,
        contact: ProspectingContact,
    ) -> None:
        score = contact.icp_score
        if score is None:
            return
        final_score = round(
            max(0.0, min(float(score.base_score) + score.score_adjustment, 100.0)),
            1,
        )
        if final_score >= 80:
            tier = "A"
        elif final_score >= 65:
            tier = "B"
        elif final_score >= 50:
            tier = "C"
        else:
            tier = "D"
        stale = score.profile_version != score.profile.version
        recommended = not stale and (
            score.review_status == "qualified"
            or (
                score.review_status != "disqualified"
                and final_score >= score.profile.minimum_score
            )
        )
        customer.custom_fields = {
            **(customer.custom_fields or {}),
            "icp_score": final_score,
            "icp_tier": tier,
            "icp_recommended": recommended,
            "icp_review_status": score.review_status,
        }
        customer.source_data_json = {
            **(customer.source_data_json or {}),
            "icp": {
                "profile_id": str(score.profile_id),
                "profile_version": score.profile_version,
                "base_score": score.base_score,
                "score_adjustment": score.score_adjustment,
                "final_score": final_score,
                "tier": tier,
                "stale": stale,
                "recommended": recommended,
                "factor_scores": dict(score.factor_scores_json or {}),
                "reasons": list(score.reasons_json or []),
                "missing_signals": list(score.missing_signals_json or []),
                "review_status": score.review_status,
                "review_reason": score.review_reason,
            },
        }

    def _connector_version(self) -> int:
        connector = (
            self._db.query(ConnectorConfiguration)
            .filter(
                ConnectorConfiguration.provider == "hunter",
                ConnectorConfiguration.enabled.is_(True),
            )
            .order_by(ConnectorConfiguration.updated_at.desc())
            .first()
        )
        return connector.version if connector is not None else 0

    @staticmethod
    def _safe_query(
        command: ProspectingSearchCreate,
        *,
        domain: Optional[str],
    ) -> Dict[str, Any]:
        query: Dict[str, Any] = {
            "domain": domain,
            "company": command.company,
        }
        if command.mode == "domain_search":
            query.update(
                {
                    "limit": command.limit,
                    "offset": command.offset,
                    "contact_type": command.contact_type,
                    "seniorities": command.seniorities,
                    "departments": command.departments,
                    "decision_maker": command.decision_maker,
                    "verification_statuses": command.verification_statuses,
                }
            )
        else:
            query.update(
                {
                    "person_name_supplied": True,
                    "max_duration": command.max_duration,
                }
            )
        return {key: value for key, value in query.items() if value is not None}

    @staticmethod
    def _normalize_domain(value: str) -> str:
        candidate = value.strip()
        parsed = urlsplit(
            candidate if "://" in candidate else f"https://{candidate}"
        )
        host = (parsed.hostname or "").rstrip(".").lower()
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("invalid domain") from exc
        if len(host) > 253 or "." not in host:
            raise ValueError("invalid domain")
        labels = host.split(".")
        if any(
            not label
            or len(label) > 63
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
            for label in labels
        ):
            raise ValueError("invalid domain")
        return host

    @staticmethod
    def _normalize_email(value: Any) -> Optional[str]:
        if not isinstance(value, str):
            return None
        try:
            return str(EMAIL_ADAPTER.validate_python(value.strip().lower()))
        except ValueError:
            return None

    @staticmethod
    def _safe_evidence(value: Any) -> List[Dict[str, Optional[str]]]:
        if not isinstance(value, list):
            return []
        evidence = []
        for item in value[:20]:
            if not isinstance(item, dict):
                continue
            uri = item.get("uri")
            if not isinstance(uri, str):
                continue
            parsed = urlsplit(uri)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                continue
            evidence.append(
                {
                    "domain": parsed.hostname.lower(),
                    "uri": uri,
                    "extracted_on": ProspectingService._text(
                        item.get("extracted_on"), 20
                    ),
                    "last_seen_on": ProspectingService._text(
                        item.get("last_seen_on"), 20
                    ),
                }
            )
            if len(evidence) == 5:
                break
        return evidence

    @staticmethod
    def _text(value: Any, limit: int) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text[:limit] or None

    @staticmethod
    def _confidence(value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return max(0, min(int(value), 100))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _customer_username(email: str) -> str:
        if len(email) <= 100:
            return email
        digest = hashlib.sha256(email.encode("utf-8")).hexdigest()[:12]
        return f"{email[:87]}-{digest}"

    @staticmethod
    def _response(row: ProspectingSearch) -> ProspectingSearchResponse:
        return ProspectingSearchResponse(
            id=row.id,
            provider=row.provider,
            mode=row.mode,
            query=dict(row.query_json or {}),
            status=row.status,
            connector_version=row.connector_version,
            result_count=row.result_count,
            error_code=row.error_code,
            created_at=row.created_at,
            completed_at=row.completed_at,
            contacts=[
                ProspectingContactResponse(
                    id=contact.id,
                    email=contact.email,
                    first_name=contact.first_name,
                    last_name=contact.last_name,
                    company=contact.company,
                    domain=contact.domain,
                    position=contact.position,
                    department=contact.department,
                    seniority=contact.seniority,
                    contact_type=contact.contact_type,
                    confidence=contact.confidence,
                    decision_maker=contact.decision_maker,
                    verification_status=contact.verification_status,
                    verification_date=contact.verification_date,
                    evidence=[
                        EvidenceReference(**item)
                        for item in (contact.evidence_json or [])
                    ],
                    imported_customer_id=contact.imported_customer_id,
                )
                for contact in row.contacts
            ],
        )
