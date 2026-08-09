"""
Celery任务函数
"""
import asyncio
from datetime import datetime
from typing import List, Dict, Any
import logging
import uuid

from app.tasks.celery_worker import celery
from app.config import settings
from app.db import SessionLocal
from app.models.database import (
    Account,
    Conversation,
    Customer,
    OutreachLog,
    OutreachStatus,
    OutboxStatus,
)
from app.core.agent import get_agent
from app.services.outbox import OutboxService
from app.services.outbox_delivery import (
    DeliveryResult,
    get_outbox_delivery_router,
)
from app.services.outreach_queue import (
    OutreachQuotaExceeded,
    OutreachQueueService,
    QueueOutreachCommand,
)

logger = logging.getLogger(__name__)


@celery.task(
    bind=True,
    max_retries=0,
    acks_late=True,
    soft_time_limit=110,
    time_limit=120,
)
def dispatch_outbox_task(
    self,
    worker_id: str = None,
    batch_size: int = 10,
):
    """Dispatch durable events without retrying an unknown send outcome."""
    worker_id = worker_id or getattr(
        self.request,
        "hostname",
        "outbox-worker",
    )
    db = SessionLocal()
    service = OutboxService(db)
    counters = {"claimed": 0, "sent": 0, "retry": 0, "dead_letter": 0}

    try:
        events = service.claim_batch(
            worker_id=worker_id,
            now=datetime.utcnow(),
            limit=batch_size,
            lease_seconds=180,
        )
        counters["claimed"] = len(events)
        db.commit()  # Persist lease before the first external side effect.

        router = get_outbox_delivery_router()
        for event in events:
            try:
                result = asyncio.run(router.deliver(event))
            except Exception:
                result = DeliveryResult.unknown_after_send(
                    "unhandled_delivery_exception"
                )

            completed_at = datetime.utcnow()
            if result.success:
                service.mark_sent(
                    event,
                    worker_id=worker_id,
                    external_message_id=result.external_message_id,
                    now=completed_at,
                )
                _sync_outreach_result(
                    db,
                    event,
                    result,
                    completed_at=completed_at,
                )
                counters["sent"] += 1
            else:
                service.mark_failure(
                    event,
                    worker_id=worker_id,
                    kind=result.failure_kind,
                    error_code=result.error_code,
                    now=completed_at,
                )
                _sync_outreach_result(
                    db,
                    event,
                    result,
                    completed_at=completed_at,
                )
                if event.status == OutboxStatus.RETRY:
                    counters["retry"] += 1
                else:
                    counters["dead_letter"] += 1
            db.commit()

        return counters
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@celery.task(bind=True, max_retries=3)
def schedule_outreach_task(
    self,
    customer_ids: List[int],
    channel: str,
    template_id: str,
    schedule_config: Dict[str, Any],
):
    """
    定时触达任务

    Args:
        customer_ids: 客户ID列表
        channel: 触达渠道
        template_id: 模板ID
        schedule_config: 调度配置
    """
    db = SessionLocal()

    try:
        # Get template
        # template = db.query(Template).filter(Template.id == template_id).first()
        # if not template:
        #     logger.error(f"Template not found: {template_id}")
        #     return {"success": False, "error": "Template not found"}

        # Get account for sending
        account = db.query(Account).filter(
            Account.account_type == channel,
            Account.is_active == True,
            Account.today_sent < Account.daily_limit
        ).first()

        if not account:
            logger.error(f"No available account for channel: {channel}")
            return {"success": False, "error": "No available account"}

        producer = OutreachQueueService(db)
        campaign_key = schedule_config.get("idempotency_key")
        if not campaign_key:
            raise ValueError("schedule_config.idempotency_key is required")

        # Create business records and outbox events in one transaction.
        results = []
        for customer_id in customer_ids:
            customer = db.query(Customer).filter(Customer.id == customer_id).first()
            if not customer:
                logger.warning(f"Customer not found: {customer_id}")
                continue

            try:
                # Check timezone and schedule time
                send_time = _calculate_send_time(customer, schedule_config)
                now = datetime.utcnow()

                if channel == "email":
                    recipient = customer.email
                    subject = "Partnership Opportunity"
                    body = (
                        f"Hi {customer.username or 'there'},\n\n"
                        "We would like to discuss a potential collaboration..."
                    )
                elif channel == "whatsapp":
                    recipient = customer.whatsapp
                    subject = None
                    body = (
                        f"Hi {customer.username or 'there'}! We would like "
                        "to discuss a potential collaboration..."
                    )
                else:
                    logger.error(f"Unsupported channel: {channel}")
                    continue

                outreach, _ = producer.queue(
                    QueueOutreachCommand(
                        customer_id=customer.id,
                        channel=channel,
                        recipient=recipient,
                        subject=subject,
                        body=body,
                        template_id=template_id,
                        account_id=account.id,
                        available_at=send_time,
                        business_key=(
                            f"scheduled:{campaign_key}:{channel}:{customer.id}"
                        ),
                    )
                )
                results.append(
                    {
                        "customer_id": customer.id,
                        "status": "queued",
                        "message_id": str(outreach.id),
                    }
                )

            except OutreachQuotaExceeded as exc:
                results.append(
                    {
                        "customer_id": customer_id,
                        "status": "failed",
                        "error": exc.error_code,
                    }
                )
            except Exception as exc:
                logger.error(
                    "Outreach queue failed for customer_id=%s error_type=%s",
                    customer_id,
                    type(exc).__name__,
                )
                results.append(
                    {
                        "customer_id": customer_id,
                        "status": "failed",
                        "error": "outreach_queue_failed",
                    }
                )

        db.commit()

        return {
            "success": True,
            "total": len(customer_ids),
            "results": results
        }

    except Exception as e:
        logger.error(f"Outreach task failed: {str(e)}")
        raise self.retry(exc=e, countdown=60 * 5)  # Retry in 5 minutes
    finally:
        db.close()


@celery.task(bind=True, max_retries=3)
def check_replies_task(self):
    """
    检查回复任务

    定期检查是否有新回复
    """
    db = SessionLocal()

    try:
        # Check for new messages in conversations
        # This would integrate with email/WhatsApp APIs to fetch new messages

        # For now, just log
        logger.info("Checking for new replies...")

        return {"success": True, "checked_at": datetime.utcnow().isoformat()}

    except Exception as e:
        logger.error(f"Check replies task failed: {str(e)}")
        raise self.retry(exc=e, countdown=60 * 5)
    finally:
        db.close()


@celery.task(bind=True)
def generate_daily_report_task(self, user_id: int):
    """
    生成日报任务

    Args:
        user_id: 用户ID
    """
    db = SessionLocal()

    try:
        today = datetime.utcnow().date()

        # Get or create stats
        from app.models.database import StatsDaily
        stats = db.query(StatsDaily).filter(
            StatsDaily.user_id == user_id,
            StatsDaily.date == today
        ).first()

        if not stats:
            stats = StatsDaily(user_id=user_id, date=today)
            db.add(stats)

        # Calculate stats from database
        stats.new_customers = db.query(Customer).filter(
            Customer.created_at >= datetime.combine(today, datetime.min.time())
        ).count()

        stats.emails_sent = db.query(OutreachLog).filter(
            OutreachLog.channel == "email",
            OutreachLog.sent_at >= datetime.combine(today, datetime.min.time())
        ).count()

        stats.whatsapp_sent = db.query(OutreachLog).filter(
            OutreachLog.channel == "whatsapp",
            OutreachLog.sent_at >= datetime.combine(today, datetime.min.time())
        ).count()

        stats.emails_replied = db.query(OutreachLog).filter(
            OutreachLog.channel == "email",
            OutreachLog.replied_at >= datetime.combine(today, datetime.min.time())
        ).count()

        stats.new_conversations = db.query(Conversation).filter(
            Conversation.created_at >= datetime.combine(today, datetime.min.time())
        ).count()

        stats.converted_customers = db.query(Customer).filter(
            Customer.status == "converted",
            Customer.updated_at >= datetime.combine(today, datetime.min.time())
        ).count()

        db.commit()

        logger.info(f"Daily report generated for user {user_id}")

        return {"success": True, "date": today.isoformat()}

    except Exception as e:
        logger.error(f"Daily report task failed: {str(e)}")
        raise self.retry(exc=e, countdown=60 * 1)
    finally:
        db.close()


@celery.task
def send_digest_email_task(user_id: int, report_data: Dict[str, Any]):
    """
    发送摘要邮件

    Args:
        user_id: 用户ID
        report_data: 报告数据
    """
    db = SessionLocal()

    try:
        # Get user
        from app.models.database import User
        user = db.query(User).filter(User.id == user_id).first()

        if not user or not user.email:
            logger.error(f"User not found or no email: {user_id}")
            return {"success": False}

        # Send digest email
        # email_service = get_email_service()
        # await email_service.send_email(...)

        logger.info(f"Digest email sent to user {user_id}")

        return {"success": True}

    except Exception as e:
        logger.error(f"Digest email task failed: {str(e)}")
        return {"success": False, "error": str(e)}
    finally:
        db.close()


@celery.task
def update_message_status_task(message_id: str):
    """
    更新消息状态任务

    Args:
        message_id: 消息ID
    """
    db = SessionLocal()

    try:
        # Update message status based on API callback
        # This would be called by webhooks from email/WhatsApp providers

        logger.info(f"Message status updated: {message_id}")

        return {"success": True}

    except Exception as e:
        logger.error(f"Update message status task failed: {str(e)}")
        return {"success": False, "error": str(e)}
    finally:
        db.close()


def _calculate_send_time(customer: Customer, schedule_config: Dict[str, Any]) -> datetime:
    """计算最佳发送时间"""
    # Simple implementation - return now
    # In production, would consider timezone and business hours
    return datetime.utcnow()


def _sync_outreach_result(
    db,
    event,
    result: DeliveryResult,
    *,
    completed_at: datetime,
) -> None:
    """Update the outreach business record in the worker transaction."""
    if event.aggregate_type != "outreach_log":
        return

    outreach = db.get(OutreachLog, uuid.UUID(event.aggregate_id))
    if outreach is None:
        raise RuntimeError("Outbox event has no outreach business record")

    if result.success:
        outreach.status = OutreachStatus.SENT
        outreach.message_id = result.external_message_id
        outreach.sent_at = completed_at
        outreach.error_msg = None
        if outreach.account_id is not None:
            account = db.get(Account, outreach.account_id)
            if account is not None:
                account.today_sent = (account.today_sent or 0) + 1
                account.last_used = completed_at
        customer = db.get(Customer, outreach.customer_id)
        if customer is not None:
            customer.first_contacted_at = (
                customer.first_contacted_at or completed_at
            )
            customer.last_contacted_at = completed_at
        return

    if event.status == OutboxStatus.DEAD_LETTER:
        outreach.status = OutreachStatus.FAILED
        outreach.error_msg = result.error_code
