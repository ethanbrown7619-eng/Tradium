"""
Settlement tracker (Phase 4): turn filled positions into REALIZED P&L.

Until a market resolves, a position's P&L is unrealized. When Polymarket resolves
a market, exactly one outcome token redeems at $1.00 and the rest at $0.00. This
module detects resolution, computes realized P&L per held token against average
cost, books it as a settlement Trade (so it flows into get_daily_loss and the
daily-loss stop), and marks the underlying trades settled.

This is what makes the daily-loss limit real for live trading — before settlement,
get_daily_loss saw no realized losses to trip on.
"""
import logging
from collections import defaultdict
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import queries

logger = logging.getLogger(__name__)


def settle_position_pnl(qty: float, avg_entry_price: float, resolved_value: float) -> float:
    """
    Realized P&L for a held position at resolution.
      resolved_value = 1.0 if this token won, else 0.0
      realized = qty * resolved_value - qty * avg_entry_price
    """
    return qty * (resolved_value - avg_entry_price)


def _aggregate_positions(trades) -> dict:
    """Average-cost net position per token_id from a set of filled trades."""
    acc: dict = {}
    for t in trades:
        tid = t.token_id
        if not tid:
            continue
        filled = float(t.filled_size or 0)
        price = float(t.fill_price or 0)
        e = acc.setdefault(tid, {"qty": 0.0, "cost": 0.0})
        if filled >= 0:
            e["qty"] += filled
            e["cost"] += filled * price
        else:
            avg = e["cost"] / e["qty"] if e["qty"] > 0 else 0.0
            sell_qty = min(-filled, e["qty"])
            e["qty"] -= sell_qty
            e["cost"] -= sell_qty * avg
    return {
        tid: {"qty": e["qty"], "avg_entry_price": (e["cost"] / e["qty"] if e["qty"] > 0 else 0.0)}
        for tid, e in acc.items() if e["qty"] > 1e-9
    }


async def settle_resolved_positions(db: AsyncSession, resolve_market) -> dict:
    """
    Find unsettled filled positions, and for any whose market has resolved, book
    realized P&L and mark the trades settled. `resolve_market(condition_id)` returns
    {"resolved": bool, "winning_token_id": str|None}.
    """
    trades = await queries.get_unsettled_filled_trades(db)
    stats = {"settled_positions": 0, "settled_trades": 0, "realized_pnl": 0.0}
    if not trades:
        return stats

    # Group by (user_id, condition_id)
    groups = defaultdict(list)
    for t in trades:
        groups[(t.user_id, t.condition_id)].append(t)

    for (user_id, condition_id), group in groups.items():
        if not condition_id:
            continue
        resolution = await resolve_market(condition_id)
        if not resolution or not resolution.get("resolved"):
            continue
        winner = resolution.get("winning_token_id")

        positions = _aggregate_positions(group)
        for token_id, pos in positions.items():
            resolved_value = 1.0 if token_id == winner else 0.0
            realized = settle_position_pnl(pos["qty"], pos["avg_entry_price"], resolved_value)
            await queries.create_trade(
                db, user_id=user_id, opportunity_id=group[0].opportunity_id,
                market_id=group[0].market_id, condition_id=condition_id, token_id=token_id,
                strategy_type="settlement", side=f"settle:{token_id[:8]}",
                size_usdc=Decimal(str(pos["qty"] * pos["avg_entry_price"])),
                fill_price=Decimal(str(resolved_value)),
                filled_size=Decimal("0"),
                fees_paid=Decimal("0"),
                profit_loss=Decimal(str(realized)),
                status="settled", is_paper=False,
                price_snapshot={"settlement": True, "resolved_value": resolved_value,
                                "avg_entry": pos["avg_entry_price"], "qty": pos["qty"]},
            )
            stats["settled_positions"] += 1
            stats["realized_pnl"] += realized

        n = await queries.mark_trades_settled(db, [t.id for t in group])
        stats["settled_trades"] += n
        logger.info(f"Settled {len(positions)} position(s) for market {condition_id}")

    return stats
