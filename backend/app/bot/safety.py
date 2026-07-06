"""
Safety controls: kill switch and naked-leg auto-unwind.

The kill switch is the hard stop — it must not merely flip a flag; it cancels
every working order on the venue (cancel_all), cancels every open opportunity,
halts the bot, and records when it fired. Nothing auto-restarts it.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import queries
from app.database.schema import UserConfig
from app.services.encryption import decrypt_private_key

logger = logging.getLogger(__name__)


async def engage_kill_switch(db: AsyncSession, config: UserConfig, order_client=None, notifier=None) -> dict:
    """
    Immediately halt trading for a user:
      1. cancel_all working orders on the CLOB (best effort),
      2. mark all working live trades cancelled,
      3. cancel all open opportunities (pending/queued/executing),
      4. set bot_active=False and stamp kill_switch_activated_at,
      5. notify if configured.
    Returns a summary of what was cancelled.
    """
    user_id = config.user_id
    result = {"orders_cancelled": False, "trades_cancelled": 0, "opportunities_cancelled": 0}

    # 1) Cancel every working order on the venue. cancel_all is the RIGHT tool here
    #    (kill switch = nuke everything); per-order cancel is for the hedge handler.
    if config.encrypted_private_key:
        try:
            if order_client is None:
                from app.services.clob import ClobOrderClient
                order_client = ClobOrderClient()
            private_key = decrypt_private_key(config.encrypted_private_key)
            result["orders_cancelled"] = await order_client.cancel_all(private_key)
        except Exception as e:
            logger.error(f"Kill switch: cancel_all failed for user {user_id}: {e}")

    # 2) + 3) Reflect cancellation in the DB
    result["trades_cancelled"] = await queries.cancel_open_trades(db, user_id)
    result["opportunities_cancelled"] = await queries.cancel_open_opportunities(db, user_id)

    # 4) Halt the bot and record the event
    settings = dict(config.settings)
    settings["bot_active"] = False
    await queries.update_user_config(
        db, user_id, settings=settings, kill_switch_activated_at=datetime.now(timezone.utc),
    )

    # 5) Notify
    email = settings.get("notify_email")
    if email and settings.get("notify_on_kill_switch", True):
        try:
            if notifier is None:
                from app.services.notifications import NotificationService
                notifier = NotificationService()
            await notifier.notify_kill_switch(email)
        except Exception as e:
            logger.error(f"Kill switch notification failed: {e}")

    logger.warning(f"KILL SWITCH engaged for user {user_id}: {result}")
    return result
