"""Deterministic, explainable ICP scoring with preserved human judgment."""

from datetime import datetime
from typing import Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.models.database import (
    ProspectingContact,
    ProspectingContactScore,
    ProspectingIcpProfile,
    ProspectingSearch,
)
from app.services.prospecting import DEPARTMENTS, SENIORITIES


DEFAULT_WEIGHTS = {
    "role_fit": 40,
    "contact_quality": 35,
    "evidence_quality": 25,
}


class IcpWeights(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_fit: int = Field(ge=0, le=100)
    contact_quality: int = Field(ge=0, le=100)
    evidence_quality: int = Field(ge=0, le=100)


class IcpProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=120)
    target_departments: List[str] = Field(default_factory=list, max_length=15)
    target_seniorities: List[str] = Field(default_factory=list, max_length=3)
    title_keywords: List[str] = Field(default_factory=list, max_length=20)
    preferred_contact_types: List[Literal["personal", "generic"]] = Field(
        default_factory=lambda: ["personal"],
        max_length=2,
    )
    weights: IcpWeights = Field(
        default_factory=lambda: IcpWeights(**DEFAULT_WEIGHTS)
    )
    minimum_score: int = Field(default=65, ge=0, le=100)

    @model_validator(mode="after")
    def validate_policy(self) -> "IcpProfileUpdate":
        if not set(self.target_departments).issubset(DEPARTMENTS):
            raise ValueError("unsupported department")
        if not set(self.target_seniorities).issubset(SENIORITIES):
            raise ValueError("unsupported seniority")
        if sum(self.weights.model_dump().values()) != 100:
            raise ValueError("ICP weights must total 100")
        return self


class IcpProfileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    target_departments: List[str]
    target_seniorities: List[str]
    title_keywords: List[str]
    preferred_contact_types: List[str]
    weights: IcpWeights
    minimum_score: int
    version: int
    created_at: datetime
    updated_at: datetime


class ProspectScoreReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_status: Literal["unreviewed", "qualified", "disqualified"]
    score_adjustment: int = Field(default=0, ge=-20, le=20)
    review_reason: Optional[str] = Field(default=None, max_length=500)


class ProspectScoreResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    contact_id: UUID
    email: str
    name: str
    company: Optional[str] = None
    domain: Optional[str] = None
    position: Optional[str] = None
    department: Optional[str] = None
    seniority: Optional[str] = None
    profile_version: int
    base_score: float
    score_adjustment: int
    final_score: float
    tier: str
    stale: bool
    recommended: bool
    factor_scores: Dict[str, float]
    reasons: List[str]
    missing_signals: List[str]
    review_status: str
    review_reason: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    scored_at: datetime


class ProspectRankingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_id: UUID
    profile_id: UUID
    profile_version: int
    minimum_score: int
    stale: bool
    scores: List[ProspectScoreResponse]


class ProspectingScoreNotFound(LookupError):
    pass


class ProspectingScoringService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_profile(self, *, user_id: int) -> IcpProfileResponse:
        return self._profile_response(self._profile(user_id=user_id))

    def update_profile(
        self,
        command: IcpProfileUpdate,
        *,
        user_id: int,
    ) -> IcpProfileResponse:
        row = self._profile(user_id=user_id)
        row.name = command.name.strip()
        row.target_departments_json = self._normalized(command.target_departments)
        row.target_seniorities_json = self._normalized(command.target_seniorities)
        row.title_keywords_json = self._normalized(command.title_keywords)
        row.preferred_contact_types_json = self._normalized(
            command.preferred_contact_types
        )
        row.weights_json = command.weights.model_dump()
        row.minimum_score = command.minimum_score
        row.version += 1
        self._db.commit()
        self._db.refresh(row)
        return self._profile_response(row)

    def score_search(
        self,
        search_id: UUID,
        *,
        user_id: int,
    ) -> ProspectRankingResponse:
        search = self._owned_search(search_id, user_id=user_id)
        profile = self._profile(user_id=user_id)
        scored_at = datetime.utcnow()
        for contact in search.contacts:
            result = self._calculate(contact, profile)
            row = (
                self._db.query(ProspectingContactScore)
                .filter(ProspectingContactScore.contact_id == contact.id)
                .one_or_none()
            )
            if row is None:
                row = ProspectingContactScore(
                    contact_id=contact.id,
                    profile_id=profile.id,
                    profile_version=profile.version,
                    base_score=result["base_score"],
                )
                self._db.add(row)
            row.profile_id = profile.id
            row.profile_version = profile.version
            row.base_score = result["base_score"]
            row.factor_scores_json = result["factor_scores"]
            row.reasons_json = result["reasons"]
            row.missing_signals_json = result["missing_signals"]
            row.scored_at = scored_at
        self._db.commit()
        return self._ranking(search, profile)

    def get_ranking(
        self,
        search_id: UUID,
        *,
        user_id: int,
    ) -> ProspectRankingResponse:
        search = self._owned_search(search_id, user_id=user_id)
        profile = self._profile(user_id=user_id)
        return self._ranking(search, profile)

    def review_score(
        self,
        score_id: UUID,
        command: ProspectScoreReview,
        *,
        user_id: int,
    ) -> ProspectScoreResponse:
        row = (
            self._db.query(ProspectingContactScore)
            .join(
                ProspectingContact,
                ProspectingContact.id == ProspectingContactScore.contact_id,
            )
            .join(
                ProspectingSearch,
                ProspectingSearch.id == ProspectingContact.search_id,
            )
            .filter(
                ProspectingContactScore.id == score_id,
                ProspectingSearch.user_id == user_id,
            )
            .one_or_none()
        )
        if row is None:
            raise ProspectingScoreNotFound("Prospect score not found")
        row.review_status = command.review_status
        row.score_adjustment = command.score_adjustment
        row.review_reason = (
            command.review_reason.strip() if command.review_reason else None
        )
        row.reviewed_by_user_id = (
            user_id if command.review_status != "unreviewed" else None
        )
        row.reviewed_at = (
            datetime.utcnow()
            if command.review_status != "unreviewed"
            else None
        )
        self._db.commit()
        self._db.refresh(row)
        return self._score_response(row, row.profile)

    def _profile(self, *, user_id: int) -> ProspectingIcpProfile:
        row = (
            self._db.query(ProspectingIcpProfile)
            .filter(ProspectingIcpProfile.user_id == user_id)
            .one_or_none()
        )
        if row is not None:
            return row
        row = ProspectingIcpProfile(
            user_id=user_id,
            name="Default decision-maker ICP",
            target_departments_json=["executive", "sales", "management"],
            target_seniorities_json=["executive", "senior"],
            title_keywords_json=["founder", "owner", "director", "head", "vp"],
            preferred_contact_types_json=["personal"],
            weights_json=dict(DEFAULT_WEIGHTS),
            minimum_score=65,
            version=1,
        )
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return row

    def _owned_search(self, search_id: UUID, *, user_id: int) -> ProspectingSearch:
        row = (
            self._db.query(ProspectingSearch)
            .filter(
                ProspectingSearch.id == search_id,
                ProspectingSearch.user_id == user_id,
            )
            .one_or_none()
        )
        if row is None:
            raise ProspectingScoreNotFound("Prospecting search not found")
        return row

    def _ranking(
        self,
        search: ProspectingSearch,
        profile: ProspectingIcpProfile,
    ) -> ProspectRankingResponse:
        score_rows = [
            contact.icp_score
            for contact in search.contacts
            if contact.icp_score is not None
        ]
        scores = [self._score_response(row, profile) for row in score_rows]
        scores.sort(key=lambda row: (-row.final_score, row.email))
        return ProspectRankingResponse(
            search_id=search.id,
            profile_id=profile.id,
            profile_version=profile.version,
            minimum_score=profile.minimum_score,
            stale=any(row.profile_version != profile.version for row in score_rows),
            scores=scores,
        )

    def _calculate(
        self,
        contact: ProspectingContact,
        profile: ProspectingIcpProfile,
    ) -> Dict[str, object]:
        reasons: List[str] = []
        missing: List[str] = []
        role_values: List[float] = []

        role_values.append(
            self._target_match(
                contact.department,
                profile.target_departments_json,
                "department",
                reasons,
                missing,
            )
        )
        role_values.append(
            self._target_match(
                contact.seniority,
                profile.target_seniorities_json,
                "seniority",
                reasons,
                missing,
            )
        )
        keywords = list(profile.title_keywords_json or [])
        if keywords:
            if not contact.position:
                role_values.append(0.5)
                missing.append("position")
            elif any(word in contact.position.lower() for word in keywords):
                role_values.append(1.0)
                reasons.append("title_keyword_match")
            else:
                role_values.append(0.0)
        role_values.append(
            1.0
            if contact.decision_maker is True
            else 0.2
            if contact.decision_maker is False
            else 0.5
        )
        if contact.decision_maker is True:
            reasons.append("decision_maker_match")
        elif contact.decision_maker is None:
            missing.append("decision_maker")

        verification = {
            "valid": 1.0,
            "accept_all": 0.55,
            "unknown": 0.25,
            "webmail": 0.2,
            "disposable": 0.0,
            "invalid": 0.0,
        }.get(contact.verification_status, 0.25)
        if contact.verification_status == "valid":
            reasons.append("verified_email")
        confidence = (
            max(0, min(contact.confidence, 100)) / 100
            if contact.confidence is not None
            else 0.5
        )
        if contact.confidence is None:
            missing.append("confidence")
        preferred_types = set(profile.preferred_contact_types_json or [])
        if contact.contact_type is None:
            contact_type = 0.5
            missing.append("contact_type")
        elif not preferred_types or contact.contact_type in preferred_types:
            contact_type = 1.0
            reasons.append("preferred_contact_type")
        else:
            contact_type = 0.3

        evidence = list(contact.evidence_json or [])
        evidence_depth = min(len(evidence) / 2, 1.0)
        if evidence:
            reasons.append("source_evidence_available")
        else:
            missing.append("evidence")
        completeness_values = [
            contact.company,
            contact.domain,
            contact.position,
            contact.first_name or contact.last_name,
        ]
        completeness = sum(bool(value) for value in completeness_values) / len(
            completeness_values
        )

        factor_scores = {
            "role_fit": round(sum(role_values) / len(role_values) * 100, 1),
            "contact_quality": round(
                (verification + confidence + contact_type) / 3 * 100,
                1,
            ),
            "evidence_quality": round(
                (evidence_depth + completeness) / 2 * 100,
                1,
            ),
        }
        weights = profile.weights_json or DEFAULT_WEIGHTS
        base_score = round(
            sum(
                factor_scores[factor] * float(weights[factor]) / 100
                for factor in DEFAULT_WEIGHTS
            ),
            1,
        )
        return {
            "base_score": base_score,
            "factor_scores": factor_scores,
            "reasons": list(dict.fromkeys(reasons)),
            "missing_signals": list(dict.fromkeys(missing)),
        }

    @staticmethod
    def _target_match(
        value: Optional[str],
        targets: List[str],
        signal: str,
        reasons: List[str],
        missing: List[str],
    ) -> float:
        if not targets:
            return 1.0
        if not value:
            missing.append(signal)
            return 0.5
        if value.lower() in targets:
            reasons.append(f"{signal}_match")
            return 1.0
        return 0.0

    @staticmethod
    def _normalized(values: List[str]) -> List[str]:
        return list(
            dict.fromkeys(
                value.strip().lower() for value in values if value.strip()
            )
        )

    @staticmethod
    def _profile_response(row: ProspectingIcpProfile) -> IcpProfileResponse:
        return IcpProfileResponse(
            id=row.id,
            name=row.name,
            target_departments=list(row.target_departments_json or []),
            target_seniorities=list(row.target_seniorities_json or []),
            title_keywords=list(row.title_keywords_json or []),
            preferred_contact_types=list(row.preferred_contact_types_json or []),
            weights=IcpWeights(**(row.weights_json or DEFAULT_WEIGHTS)),
            minimum_score=row.minimum_score,
            version=row.version,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _score_response(
        row: ProspectingContactScore,
        profile: ProspectingIcpProfile,
    ) -> ProspectScoreResponse:
        final_score = round(
            max(0.0, min(float(row.base_score) + row.score_adjustment, 100.0)),
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
        stale = row.profile_version != profile.version
        recommended = not stale and (
            row.review_status == "qualified"
            or (
                row.review_status != "disqualified"
                and final_score >= profile.minimum_score
            )
        )
        contact = row.contact
        display_name = " ".join(
            value for value in [contact.first_name, contact.last_name] if value
        ) or contact.email
        return ProspectScoreResponse(
            id=row.id,
            contact_id=contact.id,
            email=contact.email,
            name=display_name,
            company=contact.company,
            domain=contact.domain,
            position=contact.position,
            department=contact.department,
            seniority=contact.seniority,
            profile_version=row.profile_version,
            base_score=row.base_score,
            score_adjustment=row.score_adjustment,
            final_score=final_score,
            tier=tier,
            stale=stale,
            recommended=recommended,
            factor_scores=dict(row.factor_scores_json or {}),
            reasons=list(row.reasons_json or []),
            missing_signals=list(row.missing_signals_json or []),
            review_status=row.review_status,
            review_reason=row.review_reason,
            reviewed_at=row.reviewed_at,
            scored_at=row.scored_at,
        )
