"""Celery application. Run:

  celery -A app.worker.celery_app worker -Q uptime,notify,default -l info
  celery -A app.worker.celery_app worker -Q audit -c 2 -l info      (long-running crawls)
  celery -A app.worker.celery_app beat -l info     (exactly ONE beat process)

Beat only *ticks*; scheduling state lives in PostgreSQL (websites.next_check_at), and rows are
claimed with FOR UPDATE SKIP LOCKED, so even two beats could not double-run a check.
"""
from celery import Celery
from celery.schedules import crontab

from app.config import get_settings

s = get_settings()
celery = Celery("sitewatch", broker=s.redis_url, include=["app.worker.tasks"])
celery.conf.update(
    task_always_eager=s.celery_eager,
    task_eager_propagates=True,
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_default_queue="default",
    task_routes={
        "app.worker.tasks.run_uptime_check": {"queue": "uptime"},
        "app.worker.tasks.send_notification": {"queue": "notify"},
        "app.worker.tasks.run_audit": {"queue": "audit"},
    },
    task_soft_time_limit=120,
    task_time_limit=180,
    broker_connection_retry_on_startup=True,
    timezone="UTC",
    beat_schedule={
        "dispatch-due-checks": {"task": "app.worker.tasks.dispatch_due_checks", "schedule": s.dispatch_interval_s},
        "worker-heartbeat": {"task": "app.worker.tasks.heartbeat", "schedule": 30.0},
        "hourly-rollup": {"task": "app.worker.tasks.rollup_metrics", "schedule": crontab(minute=5)},
        "daily-retention": {"task": "app.worker.tasks.purge_old_checks", "schedule": crontab(hour=3, minute=17)},
        "schedule-monthly-audits": {"task": "app.worker.tasks.schedule_audits", "schedule": crontab(minute=23)},
        "fail-stuck-audits": {"task": "app.worker.tasks.fail_stuck_audits", "schedule": crontab(minute="*/30")},
        "requeue-stuck-notifications": {"task": "app.worker.tasks.requeue_pending_notifications",
                                        "schedule": crontab(minute="*/10")},
    },
)
