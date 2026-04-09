"""
Celery worker configuration.
Handles background scanning tasks and scheduled jobs.
"""
import asyncio
from celery import Celery
from celery.schedules import crontab
from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "tradium",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

# Beat schedule
celery_app.conf.beat_schedule = {
    "scan-markets": {
        "task": "app.worker.scan_markets_task",
        "schedule": 15.0,  # Every 15 seconds
    },
    "daily-summary": {
        "task": "app.worker.daily_summary_task",
        "schedule": crontab(hour=0, minute=0),  # Midnight UTC
    },
    "cleanup-expired": {
        "task": "app.worker.cleanup_expired_opportunities",
        "schedule": 300.0,  # Every 5 minutes
    },
}


def _run_async(coro):
    """Helper to run async code from sync Celery tasks."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(name="app.worker.scan_markets_task", bind=True, max_retries=1)
def scan_markets_task(self):
    """Main market scanning task."""
    try:
        from app.bot.scanner import run_scan_cycle
        _run_async(run_scan_cycle())
    except Exception as e:
        self.retry(exc=e, countdown=5)


@celery_app.task(name="app.worker.daily_summary_task")
def daily_summary_task():
    """Send daily P&L summary to all users with notifications enabled."""
    async def _send_summaries():
        from app.database.session import async_session
        from app.database.schema import UserConfig
        from app.database import queries
        from app.services.notifications import NotificationService
        from sqlalchemy import select
        from datetime import datetime, timezone

        async with async_session() as db:
            result = await db.execute(select(UserConfig))
            configs = list(result.scalars().all())

            notifier = NotificationService()
            today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

            for config in configs:
                settings = config.settings
                email = settings.get("notify_email")
                if not email or not settings.get("notify_on_daily_summary", True):
                    continue

                summary = await queries.get_trade_summary(db, config.user_id, since=today)
                await notifier.notify_daily_summary(email, summary)

    _run_async(_send_summaries())


@celery_app.task(name="app.worker.cleanup_expired_opportunities")
def cleanup_expired_opportunities():
    """Mark old pending opportunities as expired."""
    async def _cleanup():
        from app.database.session import async_session
        from app.database.schema import Opportunity
        from sqlalchemy import update
        from datetime import datetime, timezone, timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
        async with async_session() as db:
            await db.execute(
                update(Opportunity)
                .where(Opportunity.status.in_(["pending", "queued"]))
                .where(Opportunity.found_at < cutoff)
                .values(status="expired")
            )
            await db.commit()

    _run_async(_cleanup())
