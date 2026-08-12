"""
Celery worker配置
"""
from celery import Celery
from app.config import settings

# Create Celery app
celery = Celery(
    "trade_ai_agent",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.tasks.task_functions", "app.tasks.media_tasks"],
)

# Configure Celery
celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=30 * 60,  # 30 minutes
    task_soft_time_limit=25 * 60,  # 25 minutes
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=1000,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    beat_schedule={
        "dispatch-transactional-outbox": {
            "task": "app.tasks.task_functions.dispatch_outbox_task",
            "schedule": 10.0,
        },
        "recover-prospecting-jobs": {
            "task": "app.tasks.task_functions.sweep_prospecting_jobs_task",
            "schedule": 15.0,
        },
        "recover-agent-runs": {
            "task": "app.tasks.task_functions.sweep_agent_runs_task",
            "schedule": 15.0,
        },
        "execute-agent-chat-runs": {
            "task": "app.tasks.task_functions.run_agent_chat_runs_task",
            "schedule": 5.0,
        },
        "purge-agent-run-events": {
            "task": "app.tasks.task_functions.purge_agent_run_events_task",
            "schedule": 3600.0,
        },
        "cleanup-expired-media-assets": {
            "task": "app.tasks.media_tasks.cleanup_media_assets_task",
            "schedule": 3600.0,
        },
        "reconcile-media-generation-jobs": {
            "task": "app.tasks.media_tasks.reconcile_media_jobs_task",
            "schedule": float(settings.MEDIA_RECONCILE_POLL_SECONDS),
        },
    },
)

# Auto-discover tasks
celery.autodiscover_tasks(["app.tasks"])
