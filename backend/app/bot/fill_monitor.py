"""
Fill monitor (Phase 3).

Polls working live orders and reconciles their fill state into Trade rows. Its
safety-critical job is the arbitrage hedge guard: if one leg of an arb fills while
a sibling leg is still unfilled, we cancel the unfilled sibling order immediately
so the bot stops adding to an unhedged position, and flag the opportunity 'partial'
for review/unwind. Fire-and-forget order placement is exactly how arb bots bleed;
this closes that gap.
"""
import logging
from collections import defaultdict

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import queries

logger = logging.getLogger(__name__)


def assess_hedge(arb_trades: list):
    """
    Pure decision: given all legs of ONE arbitrage opportunity, decide whether the
    hedge is partial and which legs' orders to cancel.

    Partial = at least one leg fully filled AND at least one leg still working.
    In that state we cancel the still-working legs (stop chasing an unhedged book)
    and mark the opportunity partial. Returns (is_partial, trades_to_cancel).
    """
    filled = [t for t in arb_trades if t.status == "filled"]
    working = [t for t in arb_trades if t.status in ("submitted", "partial")]
    if filled and working:
        return True, working
    return False, []


async def monitor_open_orders(db: AsyncSession, order_client, resolve_key) -> dict:
    """
    Poll every working live order, update its fill state, and enforce the arb hedge
    guard per opportunity. `resolve_key(user_id) -> private_key|None` supplies the
    signing key needed to query/cancel a user's orders.
    """
    open_trades = await queries.get_open_order_trades(db)
    stats = {"polled": 0, "filled": 0, "cancelled": 0, "partial_opps": 0}
    if not open_trades:
        return stats

    by_opp = defaultdict(list)
    for t in open_trades:
        by_opp[t.opportunity_id].append(t)

    for opp_id, trades in by_opp.items():
        # 1) Poll each working order and write back its latest fill state
        for t in trades:
            pk = await resolve_key(t.user_id)
            if not pk or not t.order_id:
                continue
            status = await order_client.get_order(pk, t.order_id)
            stats["polled"] += 1
            await queries.update_trade_fill(
                db, t.id, status.status,
                filled_size=status.filled_size,
                fill_price=status.fill_price,
                settled=(status.status == "filled"),
            )
            if status.status == "filled":
                stats["filled"] += 1

        if opp_id is None:
            continue

        # 2) Hedge guard for arbitrage opportunities
        all_trades = await queries.get_trades_for_opportunity(db, opp_id)
        arb = [t for t in all_trades if t.strategy_type in ("binary", "multi_outcome")]
        if not arb:
            continue
        is_partial, to_cancel = assess_hedge(arb)
        if is_partial:
            for t in to_cancel:
                pk = await resolve_key(t.user_id)
                if pk and t.order_id:
                    await order_client.cancel_order(pk, t.order_id)
                await queries.update_trade_fill(db, t.id, "cancelled")
                stats["cancelled"] += 1
            await queries.update_opportunity_status(db, opp_id, "partial")
            stats["partial_opps"] += 1
            logger.warning(
                f"Partial hedge on opportunity {opp_id}: cancelled {len(to_cancel)} "
                f"unfilled sibling leg(s); flagged for review/unwind"
            )

    return stats
