"""
Celery任务函数
"""
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Any
import logging
import uuid

from fastapi import HTTPException

from app.tasks.celery_worker import celery
from app.config import settings
from app.db import SessionLocal
from app.models.database import (
    Account,
    AgentOutreachDelivery,
    Conversation,
    Customer,
    OutreachLog,
    OutreachStatus,
    OutboxStatus,
    ProspectingJob,
)
from app.core.agent import get_agent
from app.services.outbox import OutboxService
from app.services.agent_concurrency import (
    close_agent_concurrency_limiter,
    get_agent_concurrency_limiter,
)
from app.services.agent_runs import AgentRunService, RunLeaseConflict
from app.services.ai_chat import AIChatService
from app.services.ai_runtime import AIRuntimeService
from app.services.llm.contracts import LLMUseCase
from app.services.outbox_delivery import (
    DeliveryResult,
    get_outbox_delivery_router,
)
from app.services.outreach_queue import (
    OutreachQuotaExceeded,
    OutreachQueueService,
    QueueOutreachCommand,
    delivery_spacing_seconds,
)
from app.integrations.hunter import HunterConnectorError, build_hunter_client
from app.services.prospecting_jobs import (
    ProspectingJobRunner,
    ProspectingJobService,
    ProspectingJobStatus,
)

logger = logging.getLogger(__name__)
AGENT_CHAT_RUN_LEASE_SECONDS = 300


@celery.task(
    name="app.tasks.task_functions.sweep_agent_runs_task",
    acks_late=True,
)
def sweep_agent_runs_task():
    """Recover expired run leases and deadlines without claiming new work."""
    db = SessionLocal()
    try:
        recovered = AgentRunService(db).recover_expired(now=datetime.utcnow())
        counters = {"requeued": 0, "cancelled": 0, "unknown": 0}
        for run in recovered:
            metric = "requeued" if run.status == "queued" else run.status
            if metric in counters:
                counters[metric] += 1
        return counters
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def _resume_claimed_chat_runs(db, runs, *, worker_id: str):
    counters = {
        "completed": 0,
        "failed": 0,
        "lease_conflict": 0,
    }
    chat = AIChatService(
        db,
        AIRuntimeService(db),
        concurrency=get_agent_concurrency_limiter(),
    )
    try:
        for run in runs:
            try:
                await chat.resume_claimed(
                    run.id,
                    worker_id=worker_id,
                    fencing_token=run.fencing_token,
                )
                counters["completed"] += 1
            except RunLeaseConflict:
                counters["lease_conflict"] += 1
            except Exception as exc:
                counters["failed"] += 1
                logger.warning(
                    "Queued AI chat run failed",
                    extra={
                        "run_id": str(run.id),
                        "error_type": type(exc).__name__,
                    },
                )
    finally:
        try:
            await close_agent_concurrency_limiter()
        except Exception as exc:
            logger.warning(
                "Agent concurrency client cleanup failed",
                extra={"error_type": type(exc).__name__},
            )
    return counters


@celery.task(
    bind=True,
    name="app.tasks.task_functions.run_agent_chat_runs_task",
    max_retries=0,
    acks_late=True,
    soft_time_limit=240,
    time_limit=270,
)
def run_agent_chat_runs_task(
    self,
    worker_id: str = None,
    batch_size: int = 10,
):
    """Claim and resume a bounded batch of durable operator-chat runs."""
    if batch_size <= 0 or batch_size > 50:
        raise ValueError("batch_size must contain 1 to 50 runs")
    worker_id = str(
        worker_id
        or getattr(self.request, "hostname", "agent-chat-worker")
    )[:100]
    db = SessionLocal()
    try:
        runs = AgentRunService(db).claim_batch(
            worker_id=worker_id,
            now=datetime.utcnow(),
            limit=batch_size,
            lease_seconds=AGENT_CHAT_RUN_LEASE_SECONDS,
            use_cases=(LLMUseCase.LIVE_REPLY.value,),
        )
        counters = {
            "claimed": len(runs),
            "completed": 0,
            "failed": 0,
            "lease_conflict": 0,
        }
        if runs:
            counters.update(
                asyncio.run(
                    _resume_claimed_chat_runs(
                        db,
                        runs,
                        worker_id=worker_id,
                    )
                )
            )
        return counters
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def enqueue_prospecting_job(job_id: str, *, countdown: int = 0) -> None:
    """Queue one bounded job slice; the slice schedules its successor."""
    run_prospecting_job_task.apply_async(
        args=[str(job_id)],
        countdown=max(countdown, 0),
    )


@celery.task(
    name="app.tasks.task_functions.sweep_prospecting_jobs_task",
    acks_late=True,
)
def sweep_prospecting_jobs_task(limit: int = 100):
    """Recover durable jobs after broker, worker, or successor enqueue failure."""
    db = SessionLocal()
    try:
        job_ids = ProspectingJobService(db).list_due_job_ids(limit=limit)
        for due_job_id in job_ids:
            enqueue_prospecting_job(str(due_job_id))
        return {"enqueued": len(job_ids)}
    finally:
        db.close()


@celery.task(
    bind=True,
    max_retries=0,
    acks_late=True,
    soft_time_limit=110,
    time_limit=120,
)
def run_prospecting_job_task(self, job_id: str):
    """Execute a small, committed slice of a resumable read-only search job."""
    db = SessionLocal()
    try:
        parsed_job_id = uuid.UUID(job_id)
        job = db.get(ProspectingJob, parsed_job_id)
        expected_version = job.connector_version if job is not None else None
        try:
            hunter = build_hunter_client(
                db,
                expected_version=expected_version,
            )
        except HunterConnectorError as exc:
            result = ProspectingJobService(db).record_dispatch_failure(
                parsed_job_id,
                exc,
            )
            return result.model_dump(mode="json")
        except HTTPException:
            result = ProspectingJobService(db).record_dispatch_failure(
                parsed_job_id,
                HunterConnectorError(
                    error_code="connector_unavailable",
                    retryable=False,
                ),
            )
            return result.model_dump(mode="json")
        result = asyncio.run(
            ProspectingJobRunner(db).run_slice(
                parsed_job_id,
                hunter=hunter,
                worker_id=getattr(self.request, "hostname", "prospecting-worker"),
                max_requests=5,
            )
        )
        if result.status == ProspectingJobStatus.QUEUED:
            enqueue_prospecting_job(job_id, countdown=1)
        elif result.status == ProspectingJobStatus.RETRY_WAIT:
            delay = 30
            if result.next_attempt_at is not None:
                delay = max(
                    int((result.next_attempt_at - datetime.utcnow()).total_seconds()),
                    1,
                )
            enqueue_prospecting_job(job_id, countdown=delay)
        return result.model_dump(mode="json")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


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
    counters = {
        "claimed": 0,
        "sent": 0,
        "retry": 0,
        "dead_letter": 0,
        "expired_dead_letter": 0,
    }

    try:
        claim_time = datetime.utcnow()
        expired = service.expire_stale_leases(now=claim_time)
        counters["expired_dead_letter"] = len(expired)
        for event in expired:
            _sync_expired_outreach(db, event)

        events = service.claim_batch(
            worker_id=worker_id,
            now=claim_time,
            limit=batch_size,
            lease_seconds=180,
        )
        counters["claimed"] = len(events)
        for event in events:
            _sync_claimed_agent_delivery(db, event)
        db.commit()  # Persist lease before the first external side effect.

        router = get_outbox_delivery_router()
        bind_session = getattr(router, "bind_session", None)
        if bind_session is not None:
            bind_session(db)
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

        # Persist spacing instead of blocking this producer between messages.
        first_available_at = datetime.utcnow()
        spacing_seconds = delivery_spacing_seconds(schedule_config)

        # Create business records and outbox events in one transaction.
        results = []
        for index, customer_id in enumerate(customer_ids):
            customer = db.query(Customer).filter(Customer.id == customer_id).first()
            if not customer:
                logger.warning(f"Customer not found: {customer_id}")
                continue

            try:
                # Check timezone and schedule time
                send_time = first_available_at + timedelta(
                    seconds=index * spacing_seconds
                )

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


def _sync_outreach_result(
    db,
    event,
    result: DeliveryResult,
    *,
    completed_at: datetime,
) -> None:
    """Update the outreach business record in the worker transaction."""
    if event.aggregate_type == "agent_outreach_delivery":
        delivery = db.get(AgentOutreachDelivery, uuid.UUID(event.aggregate_id))
        if delivery is None:
            raise RuntimeError("Outbox event has no agent delivery record")
        if result.success:
            delivery.status = "sent"
            delivery.external_message_id = result.external_message_id
            delivery.verified_at = completed_at
            delivery.error_code = None
            account = db.get(Account, delivery.account_id)
            if account is not None:
                account.today_sent = (account.today_sent or 0) + 1
                account.last_used = completed_at
            customer = db.get(Customer, delivery.customer_id)
            if customer is not None:
                customer.first_contacted_at = (
                    customer.first_contacted_at or completed_at
                )
                customer.last_contacted_at = completed_at
            return
        delivery.error_code = result.error_code
        if event.status == OutboxStatus.DEAD_LETTER:
            delivery.status = (
                "awaiting_verification"
                if result.failure_kind.value == "unknown_after_send"
                else "blocked"
            )
        else:
            delivery.status = "scheduled"
        return

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


def _sync_expired_outreach(db, event) -> None:
    if event.aggregate_type == "agent_outreach_delivery":
        delivery = db.get(AgentOutreachDelivery, uuid.UUID(event.aggregate_id))
        if delivery is None:
            raise RuntimeError("Outbox event has no agent delivery record")
        delivery.status = "awaiting_verification"
        delivery.error_code = "lease_expired_unknown_delivery_state"
        return
    if event.aggregate_type != "outreach_log":
        return

    outreach = db.get(OutreachLog, uuid.UUID(event.aggregate_id))
    if outreach is None:
        raise RuntimeError("Outbox event has no outreach business record")
    outreach.status = OutreachStatus.FAILED
    outreach.error_msg = "lease_expired_unknown_delivery_state"


def _sync_claimed_agent_delivery(db, event) -> None:
    if event.aggregate_type != "agent_outreach_delivery":
        return
    delivery = db.get(AgentOutreachDelivery, uuid.UUID(event.aggregate_id))
    if delivery is None:
        raise RuntimeError("Outbox event has no agent delivery record")
    delivery.status = "dispatching"
