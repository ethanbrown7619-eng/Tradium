"""
Order reconciliation (Phase 4).

A crash between placing an order on the CLOB and recording its Trade row leaves a
LIVE order the database has no knowledge of. The fill monitor only polls orders it
knows, so such an orphan would sit on the book invisibly. Reconciliation queries
open orders from the VENUE (not the DB) and cancels any the DB doesn't recognize —
the safe default, since we can't reconstruct an unknown order's intent.
"""
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import queries

logger = logging.getLogger(__name__)


async def reconcile_orphan_orders(db: AsyncSession, order_client, resolve_key) -> dict:
    """
    For every user with a wallet, compare venue-open orders against DB-known order
    IDs and cancel the orphans. `resolve_key(user_id) -> private_key|None`.
    """
    stats = {"users": 0, "orphans_cancelled": 0}
    user_ids = await queries.get_wallet_user_ids(db)
    for user_id in user_ids:
        pk = await resolve_key(user_id)
        if not pk:
            continue
        stats["users"] += 1
        venue_orders = await order_client.get_open_orders(pk)
        known = await queries.get_known_order_ids(db, user_id)
        for o in venue_orders:
            oid = o.get("order_id")
            if oid and oid not in known:
                logger.warning(f"Orphan order {oid} for user {user_id} — cancelling (unknown to DB)")
                await order_client.cancel_order(pk, oid)
                stats["orphans_cancelled"] += 1
    return stats
