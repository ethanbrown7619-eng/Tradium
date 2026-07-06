"""
Celery worker configuration.
Handles background scanning tasks and scheduled jobs.
"""
import asyncio
import logging
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
    "poll-open-orders": {
        "task": "app.worker.poll_open_orders_task",
        "schedule": 5.0,  # Every 5 seconds — fills must be reconciled quickly
    },
    "settle-positions": {
        "task": "app.worker.settle_positions_task",
        "schedule": 300.0,  # Every 5 minutes — resolve settled markets into realized P&L
    },
    "reconcile-orders": {
        "task": "app.worker.reconcile_orders_task",
        "schedule": 120.0,  # Every 2 minutes — cancel crash-orphaned venue orders
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
    """Main market scanning task, guarded so cycles can't overlap and double-fire."""
    import redis as redis_lib
    r = redis_lib.from_url(settings.redis_url)
    # Only one scan cycle at a time: a slow cycle must not overlap the next beat.
    got_lock = r.set("tradium:scan:lock", "1", nx=True, ex=60)
    if not got_lock:
        return  # previous scan still running
    try:
        from app.bot.scanner import run_scan_cycle
        _run_async(run_scan_cycle())
    except Exception as e:
        self.retry(exc=e, countdown=5)
    finally:
        try:
            r.delete("tradium:scan:lock")
        except Exception:
            pass


@celery_app.task(name="app.worker.reconcile_orders_task")
def reconcile_orders_task():
    """Cancel venue orders the DB doesn't know about (crash-orphans)."""
    async def _rec():
        from app.database.session import async_session
        from app.database import queries
        from app.services.clob import ClobOrderClient
        from app.services.encryption import decrypt_private_key
        from app.bot.reconcile import reconcile_orphan_orders

        oc = ClobOrderClient()
        async with async_session() as db:
            cache: dict = {}

            async def resolve_key(user_id):
                if user_id in cache:
                    return cache[user_id]
                cfg = await queries.get_user_config(db, user_id)
                key = None
                if cfg and cfg.encrypted_private_key:
                    try:
                        key = decrypt_private_key(cfg.encrypted_private_key)
                    except ValueError:
                        key = None
                cache[user_id] = key
                return key

            return await reconcile_orphan_orders(db, oc, resolve_key)

    _run_async(_rec())


@celery_app.task(name="app.worker.poll_open_orders_task")
def poll_open_orders_task():
    """Poll working live orders, reconcile fills, and enforce the arb hedge guard."""
    async def _poll():
        from app.database.session import async_session
        from app.database import queries
        from app.services.clob import ClobOrderClient
        from app.services.encryption import decrypt_private_key
        from app.bot.fill_monitor import monitor_open_orders

        order_client = ClobOrderClient()
        async with async_session() as db:
            _key_cache: dict = {}

            async def resolve_key(user_id):
                if user_id in _key_cache:
                    return _key_cache[user_id]
                cfg = await queries.get_user_config(db, user_id)
                key = None
                if cfg and cfg.encrypted_private_key:
                    try:
                        key = decrypt_private_key(cfg.encrypted_private_key)
                    except ValueError:
                        key = None
                _key_cache[user_id] = key
                return key

            return await monitor_open_orders(db, order_client, resolve_key)

    _run_async(_poll())


@celery_app.task(name="app.worker.settle_positions_task")
def settle_positions_task():
    """Resolve settled markets into realized P&L so the daily-loss stop is real."""
    async def _settle():
        from app.database.session import async_session
        from app.services.polymarket import PolymarketService
        from app.bot.settlement import settle_resolved_positions

        pm = PolymarketService()
        async with async_session() as db:
            return await settle_resolved_positions(db, pm.get_market_resolution)

    _run_async(_settle())


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
    """
    Expire stale open opportunities and reap orphaned 'executing' claims.
    The reaper is a liveness/stuck-capital safeguard: without it, an executor crash
    after the atomic queued->executing claim would strand an opportunity forever.
    """
    async def _cleanup():
        from app.database.session import async_session
        from app.database import queries

        async with async_session() as db:
            expired = await queries.expire_stale_opportunities(db, older_than_seconds=300)
            reaped = await queries.reap_orphaned_executing(db, older_than_seconds=120)
            if reaped:
                logging.getLogger(__name__).warning(
                    f"Reaped {reaped} orphaned 'executing' opportunities -> failed"
                )
            return expired, reaped

    _run_async(_cleanup())
