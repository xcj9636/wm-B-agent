"""
客户管理相关API
"""
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.db import get_db
from app.integrations.hunter import (
    EmailVerificationResult,
    HunterClient,
    HunterConnectorError,
    get_hunter_client,
)
from app.models.database import ContactVerification, User, Customer, IntentLevel
from app.models.schemas import (
    CustomerCreate, CustomerUpdate, CustomerResponse,
    CustomerListResponse, HighIntentLeadResponse, SearchFilters
)
from app.api.v1.auth import get_current_active_user

router = APIRouter()


class ContactVerificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    status: str
    score: Optional[int] = None
    retryable: bool
    legal_restricted: bool
    details: Dict[str, Any]


SAFE_VERIFICATION_DETAILS = {
    "regexp",
    "gibberish",
    "disposable",
    "webmail",
    "mx_records",
    "smtp_server",
    "smtp_check",
    "accept_all",
    "block",
}


def _record_verification(
    db: Session,
    customer: Customer,
    result: EmailVerificationResult,
    *,
    legal_restricted: bool = False,
) -> ContactVerificationResponse:
    safe_details = {
        key: value
        for key, value in result.details.items()
        if key in SAFE_VERIFICATION_DETAILS
    }
    record = ContactVerification(
        customer_id=customer.id,
        email=customer.email,
        provider="hunter",
        status=result.status,
        score=result.score,
        retryable=result.retryable,
        legal_restricted=legal_restricted,
        details_json=safe_details,
    )
    fields = dict(customer.custom_fields or {})
    fields["email_verification_status"] = result.status
    if legal_restricted or result.status in {"invalid", "disposable"}:
        fields["contact_suppressed"] = True
        fields["suppression_reason"] = (
            "legal_restriction"
            if legal_restricted
            else f"email_{result.status}"
        )
    elif result.status == "valid":
        suppression_reason = str(fields.get("suppression_reason") or "")
        if not fields.get("contact_suppressed") or suppression_reason.startswith("email_"):
            fields["contact_suppressed"] = False
            fields.pop("suppression_reason", None)
    customer.custom_fields = fields
    db.add(record)
    db.commit()
    return ContactVerificationResponse(
        status=result.status,
        score=result.score,
        retryable=result.retryable,
        legal_restricted=legal_restricted,
        details=safe_details,
    )


@router.get("", response_model=CustomerListResponse)
async def list_customers(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    platform: Optional[str] = None,
    country: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    intent_level: Optional[str] = None,
    search: Optional[str] = None,
    _: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """列出客户"""
    query = db.query(Customer)

    # Apply filters
    if platform:
        query = query.filter(Customer.platform == platform)
    if country:
        query = query.filter(Customer.country == country)
    if category:
        query = query.filter(Customer.category == category)
    if status:
        query = query.filter(Customer.status == status)
    if intent_level:
        query = query.filter(Customer.intent_level == intent_level)
    if search:
        query = query.filter(
            (Customer.username.ilike(f"%{search}%")) |
            (Customer.email.ilike(f"%{search}%"))
        )

    # Count total
    total = query.count()

    # Pagination
    items = query.offset((page - 1) * page_size).limit(page_size).all()

    return CustomerListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size
    )


@router.post("", response_model=CustomerResponse)
async def create_customer(
    customer: CustomerCreate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """创建客户"""
    # Check for duplicates
    existing = db.query(Customer).filter(
        Customer.username == customer.username,
        Customer.platform == customer.platform
    ).first()

    if existing:
        raise HTTPException(status_code=400, detail="Customer already exists")

    db_customer = Customer(**customer.model_dump())
    db.add(db_customer)
    db.commit()
    db.refresh(db_customer)

    return db_customer


@router.get("/high-intent", response_model=List[HighIntentLeadResponse])
async def list_high_intent_customers(
    limit: int = Query(10, ge=1, le=100),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Return the dashboard's highest-priority customer projection."""
    customers = db.query(Customer).filter(
        Customer.intent_level.in_([IntentLevel.HIGH, IntentLevel.VERY_HIGH])
    ).order_by(Customer.updated_at.desc()).limit(limit).all()

    return [
        HighIntentLeadResponse(
            id=customer.id,
            name=(
                customer.company_name
                or customer.username
                or customer.email
                or f"Customer {customer.id}"
            ),
            intent=customer.intent_level.value,
            platform=customer.platform,
        )
        for customer in customers
    ]


@router.post(
    "/{customer_id}/email-verification",
    response_model=ContactVerificationResponse,
)
async def verify_customer_email(
    customer_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
    hunter: HunterClient = Depends(get_hunter_client),
):
    """Verify one saved contact and persist a suppression-safe audit result."""
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    if not customer.email:
        raise HTTPException(status_code=409, detail="Customer email is not configured")
    try:
        result = await hunter.verify_email(customer.email)
    except HunterConnectorError as exc:
        if exc.legal_restriction:
            _record_verification(
                db,
                customer,
                EmailVerificationResult(
                    status="legal_restricted",
                    retryable=False,
                ),
                legal_restricted=True,
            )
            raise HTTPException(
                status_code=451,
                detail="Contact is legally restricted and has been suppressed",
            ) from exc
        raise HTTPException(
            status_code=503 if exc.retryable else 422,
            detail=exc.error_code,
        ) from exc
    return _record_verification(db, customer, result)


@router.get("/{customer_id}", response_model=CustomerResponse)
async def get_customer(
    customer_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """获取客户详情"""
    customer = db.query(Customer).filter(Customer.id == customer_id).first()

    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    return customer


@router.put("/{customer_id}", response_model=CustomerResponse)
async def update_customer(
    customer_id: int,
    customer_update: CustomerUpdate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """更新客户信息"""
    customer = db.query(Customer).filter(Customer.id == customer_id).first()

    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    update_data = customer_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(customer, key, value)

    db.commit()
    db.refresh(customer)

    return customer


@router.delete("/{customer_id}")
async def delete_customer(
    customer_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """删除客户"""
    customer = db.query(Customer).filter(Customer.id == customer_id).first()

    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    db.delete(customer)
    db.commit()

    return {"message": "Customer deleted"}


@router.post("/bulk")
async def bulk_create_customers(
    customers: List[CustomerCreate],
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """批量创建客户"""
    created = []
    duplicates = 0

    for customer_data in customers:
        # Check for duplicates
        existing = db.query(Customer).filter(
            Customer.username == customer_data.username,
            Customer.platform == customer_data.platform
        ).first()

        if existing:
            duplicates += 1
            continue

        db_customer = Customer(**customer_data.model_dump())
        db.add(db_customer)
        db.flush()
        created.append(db_customer)

    db.commit()

    return {
        "created": len(created),
        "duplicates": duplicates,
        "customers": [c.id for c in created]
    }


@router.post("/{customer_id}/tags")
async def add_tags(
    customer_id: int,
    tags: List[str],
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """为客户添加标签"""
    customer = db.query(Customer).filter(Customer.id == customer_id).first()

    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    current_tags = customer.tags_json or []
    new_tags = list(set(current_tags + tags))
    customer.tags_json = new_tags

    db.commit()

    return {"tags": new_tags}


@router.delete("/{customer_id}/tags")
async def remove_tags(
    customer_id: int,
    tags: List[str],
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """从客户移除标签"""
    customer = db.query(Customer).filter(Customer.id == customer_id).first()

    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    current_tags = customer.tags_json or []
    new_tags = [t for t in current_tags if t not in tags]
    customer.tags_json = new_tags

    db.commit()

    return {"tags": new_tags}
