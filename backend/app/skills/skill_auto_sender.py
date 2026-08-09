"""
Skill 5: 自动化触达发送

功能：
- 按时区定时发送
- 随机间隔防封
- 多账号轮换
- 状态回传
"""
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import random

from app.core.skill_base import BaseSkill, register_skill
from app.core.context import ExecutionContext
from app.db import SessionLocal
from app.services.outreach_queue import (
    OutreachQuotaExceeded,
    OutreachQueueService,
    QueueOutreachCommand,
    delivery_spacing_seconds,
)


@register_skill
class AutoSenderSkill(BaseSkill):
    """
    自动发送Skill

    自动发送邮件或WhatsApp消息
    """
    name = "auto_sender"
    display_name = "Auto Sender"
    description = "自动化触达发送，支持邮件和WhatsApp"
    category = "outreach"
    version = "1.0.0"

    config_schema = {
        "type": "object",
        "properties": {
            "dry_run": {
                "type": "boolean",
                "default": False,
                "description": "是否只模拟发送，不实际发送"
            },
            "batch_size": {
                "type": "integer",
                "default": 50,
                "description": "每批发送数量"
            },
            "default_timezone": {
                "type": "string",
                "default": "UTC"
            },
            "enable_account_rotation": {
                "type": "boolean",
                "default": True,
                "description": "是否启用账号轮换"
            }
        }
    }

    default_config = {
        "dry_run": False,
        "batch_size": 50,
        "default_timezone": "UTC",
        "enable_account_rotation": True
    }

    input_schema = {
        "type": "object",
        "required": ["customers", "messages"],
        "properties": {
            "customers": {
                "type": "array",
                "items": {"type": "object"},
                "description": "客户列表"
            },
            "messages": {
                "type": "object",
                "description": "消息内容（可以是单个消息或批量消息）"
            },
            "channel": {
                "type": "string",
                "enum": ["email", "whatsapp"],
                "default": "email"
            },
            "schedule": {
                "type": "object",
                "description": "发送计划"
            },
            "accounts": {
                "type": "array",
                "items": {"type": "object"},
                "description": "可用的账号列表"
            },
            "send_immediately": {
                "type": "boolean",
                "default": False
            }
        }
    }

    output_schema = {
        "type": "object",
        "required": ["results", "success_count", "failed_count"],
        "properties": {
            "results": {
                "type": "array",
                "description": "发送结果列表"
            },
            "success_count": {
                "type": "integer"
            },
            "failed_count": {
                "type": "integer"
            },
            "scheduled_count": {
                "type": "integer"
            },
            "queued_count": {
                "type": "integer"
            }
        }
    }

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._account_pool: List[Dict[str, Any]] = []
        self._current_account_index = 0

    async def execute(self, context: ExecutionContext) -> Dict[str, Any]:
        """
        Execute auto sending

        Args:
            context: Execution context

        Returns:
            Dict containing send results
        """
        input_data = context.input_data
        customers = input_data.get("customers", [])
        messages = input_data.get("messages", {})
        channel = input_data.get("channel", "email")
        schedule = input_data.get("schedule", {})
        accounts = input_data.get("accounts", [])
        send_immediately = input_data.get("send_immediately", False)

        dry_run = self.config.get("dry_run", False)
        enable_rotation = self.config.get("enable_account_rotation", True)

        # Initialize account pool
        if enable_rotation and accounts:
            self._account_pool = accounts.copy()
            random.shuffle(self._account_pool)

        results = []
        success_count = 0
        failed_count = 0
        scheduled_count = 0
        queued_count = 0
        spacing_seconds = delivery_spacing_seconds(schedule)

        # Process messages
        if isinstance(messages, list) and len(messages) == len(customers):
            # Bulk messages - one per customer
            for i, (customer, message) in enumerate(zip(customers, messages)):
                result = await self._send_single(
                    customer,
                    message,
                    channel,
                    schedule,
                    send_immediately,
                    dry_run,
                    business_key=(
                        f"auto:{context.execution_id}:{i}:{channel}"
                    ),
                    queue_delay_seconds=i * spacing_seconds,
                )
                results.append(result)

                if result["status"] == "sent":
                    success_count += 1
                elif result["status"] == "scheduled":
                    scheduled_count += 1
                elif result["status"] == "queued":
                    queued_count += 1
                else:
                    failed_count += 1

        else:
            # Single message for all customers
            for i, customer in enumerate(customers):
                result = await self._send_single(
                    customer,
                    messages,
                    channel,
                    schedule,
                    send_immediately,
                    dry_run,
                    business_key=(
                        f"auto:{context.execution_id}:{i}:{channel}"
                    ),
                    queue_delay_seconds=i * spacing_seconds,
                )
                results.append(result)

                if result["status"] == "sent":
                    success_count += 1
                elif result["status"] == "scheduled":
                    scheduled_count += 1
                elif result["status"] == "queued":
                    queued_count += 1
                else:
                    failed_count += 1

        # Update metrics
        context.set_state("send_stats", {
            "success": success_count,
            "failed": failed_count,
            "scheduled": scheduled_count,
            "queued": queued_count,
        })
        context.increment_metric("messages_sent", success_count)
        context.increment_metric("messages_failed", failed_count)
        context.increment_metric("messages_queued", queued_count)

        return {
            "results": results,
            "success_count": success_count,
            "failed_count": failed_count,
            "scheduled_count": scheduled_count,
            "queued_count": queued_count,
        }

    async def _send_single(
        self,
        customer: Dict[str, Any],
        message: Dict[str, Any],
        channel: str,
        schedule: Optional[Dict[str, Any]],
        send_immediately: bool,
        dry_run: bool,
        business_key: str,
        queue_delay_seconds: int,
    ) -> Dict[str, Any]:
        """Send a single message"""
        result = {
            "customer_id": customer.get("id"),
            "customer_username": customer.get("username"),
            "status": "pending",
            "message_id": None,
            "sent_at": None,
            "error": None
        }

        try:
            available_at = datetime.utcnow() + timedelta(
                seconds=queue_delay_seconds
            )
            # Check if should schedule instead of immediate send
            if not send_immediately and schedule:
                available_at = self._calculate_send_time(
                    customer,
                    schedule,
                ) + timedelta(seconds=queue_delay_seconds)

            # Get account
            account = self._get_next_account(channel, customer)

            if dry_run:
                result["status"] = "sent"
                result["sent_at"] = datetime.utcnow().isoformat()
                return result

            if channel == "email":
                recipient = customer.get("email")
                subject = message.get("subject", "")
                body = message.get("body", "")
            elif channel == "whatsapp":
                recipient = customer.get("whatsapp")
                subject = None
                body = message.get("whatsapp_message") or message.get("body", "")
            else:
                raise ValueError(f"Unsupported channel: {channel}")

            db = SessionLocal()
            try:
                outreach, _ = OutreachQueueService(db).queue(
                    QueueOutreachCommand(
                        customer_id=customer.get("id"),
                        channel=channel,
                        recipient=recipient,
                        subject=subject,
                        body=body,
                        account_id=account.get("id") if account else None,
                        available_at=available_at,
                        business_key=business_key,
                    )
                )
                db.commit()
                result["message_id"] = str(outreach.id)
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

            if available_at > datetime.utcnow():
                result["status"] = "scheduled"
                result["scheduled_at"] = available_at.isoformat()
            else:
                result["status"] = "queued"
            result["account_id"] = account.get("id") if account else None

        except OutreachQuotaExceeded as exc:
            result["status"] = "failed"
            result["error"] = exc.error_code
        except Exception:
            result["status"] = "failed"
            result["error"] = "outreach_queue_failed"

        return result

    def _get_next_account(self, channel: str, customer: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Get next account from rotation pool"""
        if not self._account_pool:
            return None

        # Filter accounts by channel
        channel_accounts = [
            acc for acc in self._account_pool
            if acc.get("account_type") == channel or acc.get("type") == channel
        ]

        if not channel_accounts:
            return self._account_pool[0]

        # Get next account
        account = channel_accounts[self._current_account_index % len(channel_accounts)]
        self._current_account_index += 1

        return account

    def _calculate_send_time(
        self,
        customer: Dict[str, Any],
        schedule: Dict[str, Any],
    ) -> datetime:
        """Calculate optimal send time based on schedule"""
        # Get timezone (default to UTC)
        timezone = schedule.get("timezone", "UTC")
        country = customer.get("country", "US")

        # Map country to timezone (simplified)
        tz_map = {
            "US": "America/New_York",
            "UK": "Europe/London",
            "DE": "Europe/Berlin",
            "FR": "Europe/Paris",
            "IT": "Europe/Rome",
            "ES": "Europe/Madrid",
            "JP": "Asia/Tokyo",
            "KR": "Asia/Seoul",
            "CN": "Asia/Shanghai",
            "SG": "Asia/Singapore",
            "AU": "Australia/Sydney",
            "BR": "America/Sao_Paulo",
            "IN": "Asia/Kolkata",
        }

        target_tz = tz_map.get(country, timezone)

        # Get allowed hours
        send_hours = schedule.get("send_hours", [9, 10, 11, 14, 15, 16])

        # Calculate random delay
        interval_min = schedule.get("interval_min", 30)
        interval_max = schedule.get("interval_max", 120)
        delay_minutes = random.randint(interval_min, interval_max)

        # Calculate send time
        now = datetime.utcnow() + timedelta(minutes=delay_minutes)

        # Simple implementation - just return delayed time
        # In production, would convert to target timezone and adjust to business hours
        return now

@register_skill
class ScheduleOutreachSkill(BaseSkill):
    """
    定时触达Skill

    为触达任务创建定时任务
    """
    name = "schedule_outreach"
    display_name = "Schedule Outreach"
    description = "创建定时触达任务"
    category = "outreach"
    version = "1.0.0"

    input_schema = {
        "type": "object",
        "required": ["customers", "channel"],
        "properties": {
            "customers": {
                "type": "array",
                "items": {"type": "object"}
            },
            "channel": {
                "type": "string",
                "enum": ["email", "whatsapp"]
            },
            "schedule": {
                "type": "object"
            },
            "template_id": {
                "type": "string"
            }
        }
    }

    output_schema = {
        "type": "object",
        "required": ["task_id", "scheduled_count"],
        "properties": {
            "task_id": {"type": "string"},
            "scheduled_count": {"type": "integer"}
        }
    }

    async def execute(self, context: ExecutionContext) -> Dict[str, Any]:
        """Execute scheduled outreach"""
        # Import task scheduling (would use Celery in production)
        from app.tasks.task_functions import schedule_outreach_task

        input_data = context.input_data
        customers = input_data.get("customers", [])
        channel = input_data.get("channel", "email")
        schedule = input_data.get("schedule", {})

        # Schedule task
        task_id = str(hash(str(customers) + channel + str(schedule)))

        # In production, this would create Celery tasks
        context.set_state("scheduled_outreach", {
            "task_id": task_id,
            "channel": channel,
            "count": len(customers)
        })

        return {
            "task_id": task_id,
            "scheduled_count": len(customers)
        }
