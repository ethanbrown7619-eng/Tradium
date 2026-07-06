from sqlalchemy import select, update, delete, func, and_, or_, desc
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta, timezone
from uuid import UUID
from typing import Optional
from decimal import Decimal

from app.database.schema import User, UserConfig, Opportunity, Trade, MarketCache, Strategy


# ── User queries ──

async def get_user_by_email(session: AsyncSession, email: str) -> Optional[User]:
    result = await session.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_user_by_id(session: AsyncSession, user_id: UUID) -> Optional[User]:
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


async def create_user(session: AsyncSession, email: str, password_hash: str) -> User:
    user = User(email=email, password_hash=password_hash)
    session.add(user)
    await session.flush()
    # Create default config
    config = UserConfig(user_id=user.id)
    session.add(config)
    await session.commit()
    await session.refresh(user)
    return user


# ── Config queries ──

async def get_user_config(session: AsyncSession, user_id: UUID) -> Optional[UserConfig]:
    result = await session.execute(select(UserConfig).where(UserConfig.user_id == user_id))
    return result.scalar_one_or_none()


async def update_user_config(session: AsyncSession, user_id: UUID, **kwargs) -> UserConfig:
    config = await get_user_config(session, user_id)
    if not config:
        config = UserConfig(user_id=user_id, **kwargs)
        session.add(config)
    else:
        for key, value in kwargs.items():
            setattr(config, key, value)
    await session.commit()
    await session.refresh(config)
    return config


# ── Opportunity queries ──

async def create_opportunity(session: AsyncSession, **kwargs) -> Opportunity:
    opp = Opportunity(**kwargs)
    session.add(opp)
    await session.commit()
    await session.refresh(opp)
    return opp


async def get_opportunities(
    session: AsyncSession,
    user_id: UUID,
    status: Optional[str] = None,
    strategy_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Opportunity]:
    q = select(Opportunity).where(Opportunity.user_id == user_id)
    if status:
        q = q.where(Opportunity.status == status)
    if strategy_type:
        q = q.where(Opportunity.strategy_type == strategy_type)
    q = q.order_by(desc(Opportunity.found_at)).limit(limit).offset(offset)
    result = await session.execute(q)
    return list(result.scalars().all())


async def update_opportunity_status(
    session: AsyncSession, opp_id: UUID, status: str, **kwargs
) -> Optional[Opportunity]:
    result = await session.execute(select(Opportunity).where(Opportunity.id == opp_id))
    opp = result.scalar_one_or_none()
    if opp:
        opp.status = status
        for k, v in kwargs.items():
            setattr(opp, k, v)
        await session.commit()
        await session.refresh(opp)
    return opp


async def claim_opportunity(
    session: AsyncSession, opp_id: UUID, expected_status: str = "queued", new_status: str = "executing"
) -> bool:
    """
    Atomically transition an opportunity from expected_status -> new_status.

    Returns True only if THIS caller won the claim (the row was in expected_status
    and this UPDATE moved it). Overlapping scan cycles racing on the same queued
    opportunity will see exactly one True; every other caller gets False and must
    skip execution. This is the guard against double-firing a single opportunity.
    """
    result = await session.execute(
        update(Opportunity)
        .where(and_(Opportunity.id == opp_id, Opportunity.status == expected_status))
        .values(status=new_status)
    )
    await session.commit()
    return result.rowcount == 1


def _orphaned_executing_stmt(cutoff: datetime):
    """UPDATE stmt: reap opportunities stuck in 'executing' since before `cutoff` -> 'failed'."""
    return (
        update(Opportunity)
        .where(and_(Opportunity.status == "executing", Opportunity.found_at < cutoff))
        .values(status="failed")
    )


def _stale_open_stmt(cutoff: datetime):
    """UPDATE stmt: expire still-open (pending/queued) opportunities older than `cutoff`."""
    return (
        update(Opportunity)
        .where(and_(Opportunity.status.in_(["pending", "queued"]), Opportunity.found_at < cutoff))
        .values(status="expired")
    )


async def reap_orphaned_executing(session: AsyncSession, older_than_seconds: int = 120) -> int:
    """
    Reset opportunities stranded in 'executing' (executor crashed mid-flight) to 'failed'.

    The atomic claim commits queued->executing before execution work runs, so a crash
    would otherwise leave the row 'executing' forever — and the claim guard guarantees
    it is never retried. We reset to 'failed' (not 'queued') so a possibly half-placed
    live order is never silently auto-retried; it surfaces for inspection instead.
    Execution takes seconds, so anything 'executing' for >2 min is orphaned.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)
    result = await session.execute(_orphaned_executing_stmt(cutoff))
    await session.commit()
    return result.rowcount


async def expire_stale_opportunities(session: AsyncSession, older_than_seconds: int = 300) -> int:
    """Expire pending/queued opportunities older than `older_than_seconds`."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)
    result = await session.execute(_stale_open_stmt(cutoff))
    await session.commit()
    return result.rowcount


# ── Trade queries ──

async def create_trade(session: AsyncSession, **kwargs) -> Trade:
    trade = Trade(**kwargs)
    session.add(trade)
    await session.commit()
    await session.refresh(trade)
    return trade


async def get_trades(
    session: AsyncSession,
    user_id: UUID,
    strategy_type: Optional[str] = None,
    status: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Trade]:
    q = select(Trade).where(Trade.user_id == user_id)
    if strategy_type:
        q = q.where(Trade.strategy_type == strategy_type)
    if status:
        q = q.where(Trade.status == status)
    if start_date:
        q = q.where(Trade.executed_at >= start_date)
    if end_date:
        q = q.where(Trade.executed_at <= end_date)
    q = q.order_by(desc(Trade.executed_at)).limit(limit).offset(offset)
    result = await session.execute(q)
    return list(result.scalars().all())


async def cancel_open_opportunities(session: AsyncSession, user_id: UUID) -> int:
    """Cancel all of a user's still-open opportunities (kill switch)."""
    result = await session.execute(
        update(Opportunity)
        .where(and_(
            Opportunity.user_id == user_id,
            Opportunity.status.in_(["pending", "queued", "executing"]),
        ))
        .values(status="cancelled")
    )
    await session.commit()
    return result.rowcount


async def cancel_open_trades(session: AsyncSession, user_id: UUID) -> int:
    """Mark a user's working LIVE order trades cancelled (kill switch)."""
    result = await session.execute(
        update(Trade)
        .where(and_(
            Trade.user_id == user_id,
            Trade.is_paper == False,  # noqa: E712
            Trade.status.in_(["submitted", "partial", "open"]),
        ))
        .values(status="cancelled")
    )
    await session.commit()
    return result.rowcount


async def get_open_order_trades(session: AsyncSession, limit: int = 500) -> list[Trade]:
    """Live trades whose orders are still working (submitted/partial) — for fill polling."""
    result = await session.execute(
        select(Trade)
        .where(and_(Trade.is_paper == False, Trade.status.in_(["submitted", "partial"])))  # noqa: E712
        .order_by(Trade.executed_at)
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_unsettled_filled_trades(session: AsyncSession, limit: int = 1000) -> list[Trade]:
    """Live, filled trades not yet settled — candidates for settlement P&L."""
    result = await session.execute(
        select(Trade)
        .where(and_(
            Trade.is_paper == False,  # noqa: E712
            Trade.status == "filled",
            Trade.settled_at.is_(None),
            Trade.strategy_type.in_(["binary", "multi_outcome", "strategy"]),
        ))
        .order_by(Trade.executed_at)
        .limit(limit)
    )
    return list(result.scalars().all())


async def mark_trades_settled(session: AsyncSession, trade_ids: list) -> int:
    if not trade_ids:
        return 0
    result = await session.execute(
        update(Trade).where(Trade.id.in_(trade_ids)).values(settled_at=func.now())
    )
    await session.commit()
    return result.rowcount


async def update_trade_fill(
    session: AsyncSession, trade_id: UUID, status: str,
    filled_size: Optional[float] = None, fill_price: Optional[float] = None,
    settled: bool = False,
) -> Optional[Trade]:
    """Update a trade with the latest fill state from the CLOB."""
    result = await session.execute(select(Trade).where(Trade.id == trade_id))
    trade = result.scalar_one_or_none()
    if not trade:
        return None
    trade.status = status
    if filled_size is not None:
        trade.filled_size = filled_size
    if fill_price is not None:
        trade.fill_price = fill_price
    if settled:
        trade.settled_at = func.now()
    await session.commit()
    await session.refresh(trade)
    return trade


async def get_known_order_ids(session: AsyncSession, user_id: UUID) -> set:
    """All CLOB order IDs the DB knows about for a user (for orphan reconciliation)."""
    result = await session.execute(
        select(Trade.order_id).where(and_(Trade.user_id == user_id, Trade.order_id.isnot(None)))
    )
    return {row[0] for row in result.all() if row[0]}


async def get_wallet_user_ids(session: AsyncSession) -> list:
    """User IDs that have a wallet configured (candidates for order reconciliation)."""
    result = await session.execute(
        select(UserConfig.user_id).where(UserConfig.encrypted_private_key.isnot(None))
    )
    return [row[0] for row in result.all()]


async def get_trades_for_opportunity(session: AsyncSession, opportunity_id: UUID) -> list[Trade]:
    result = await session.execute(
        select(Trade).where(Trade.opportunity_id == opportunity_id)
    )
    return list(result.scalars().all())


async def get_trade_summary(session: AsyncSession, user_id: UUID, since: Optional[datetime] = None) -> dict:
    q = select(
        func.count(Trade.id).label("total_trades"),
        func.sum(Trade.profit_loss).label("total_pnl"),
        func.sum(Trade.fees_paid).label("total_fees"),
        func.sum(Trade.size_usdc).label("total_volume"),
        func.count(Trade.id).filter(Trade.profit_loss > 0).label("winning_trades"),
    ).where(Trade.user_id == user_id)
    if since:
        q = q.where(Trade.executed_at >= since)
    result = await session.execute(q)
    row = result.one()
    total = row.total_trades or 0
    return {
        "total_trades": total,
        "total_pnl": float(row.total_pnl or 0),
        "total_fees": float(row.total_fees or 0),
        "total_volume": float(row.total_volume or 0),
        "winning_trades": row.winning_trades or 0,
        "win_rate": (row.winning_trades or 0) / total if total > 0 else 0,
    }


async def get_daily_loss(session: AsyncSession, user_id: UUID) -> float:
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    result = await session.execute(
        select(func.sum(Trade.profit_loss))
        .where(and_(Trade.user_id == user_id, Trade.executed_at >= today, Trade.profit_loss < 0))
    )
    return abs(float(result.scalar() or 0))


async def get_trades_last_hour(session: AsyncSession, user_id: UUID) -> int:
    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    result = await session.execute(
        select(func.count(Trade.id))
        .where(and_(Trade.user_id == user_id, Trade.executed_at >= one_hour_ago))
    )
    return result.scalar() or 0


async def get_capital_deployed(session: AsyncSession, user_id: UUID) -> float:
    """Sum of size_usdc for trades that are pending/submitted (not yet settled)."""
    result = await session.execute(
        select(func.sum(Trade.size_usdc))
        .where(and_(
            Trade.user_id == user_id,
            Trade.status.in_(["pending", "submitted", "filled"]),
            Trade.settled_at.is_(None),
        ))
    )
    return float(result.scalar() or 0)


# ── Market cache queries ──

async def upsert_market_cache(session: AsyncSession, market_id: str, condition_id: str, data: dict):
    existing = await session.get(MarketCache, market_id)
    if existing:
        existing.data = data
        existing.condition_id = condition_id
        existing.cached_at = func.now()
    else:
        session.add(MarketCache(market_id=market_id, condition_id=condition_id, data=data))
    await session.commit()


async def get_cached_markets(session: AsyncSession, max_age_seconds: int = 300) -> list[MarketCache]:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
    result = await session.execute(
        select(MarketCache).where(MarketCache.cached_at >= cutoff)
    )
    return list(result.scalars().all())


# ── Strategy queries ──

async def create_strategy(session: AsyncSession, user_id: UUID, name: str, definition: dict, enabled: bool = False) -> Strategy:
    strat = Strategy(user_id=user_id, name=name, definition=definition, enabled=enabled)
    session.add(strat)
    await session.commit()
    await session.refresh(strat)
    return strat


async def get_strategies(session: AsyncSession, user_id: UUID, enabled_only: bool = False) -> list[Strategy]:
    q = select(Strategy).where(Strategy.user_id == user_id)
    if enabled_only:
        q = q.where(Strategy.enabled == True)  # noqa: E712
    q = q.order_by(desc(Strategy.created_at))
    result = await session.execute(q)
    return list(result.scalars().all())


async def get_strategy(session: AsyncSession, strategy_id: UUID) -> Optional[Strategy]:
    result = await session.execute(select(Strategy).where(Strategy.id == strategy_id))
    return result.scalar_one_or_none()


async def update_strategy(session: AsyncSession, strategy_id: UUID, **kwargs) -> Optional[Strategy]:
    strat = await get_strategy(session, strategy_id)
    if strat:
        for k, v in kwargs.items():
            setattr(strat, k, v)
        await session.commit()
        await session.refresh(strat)
    return strat


async def delete_strategy(session: AsyncSession, strategy_id: UUID) -> bool:
    result = await session.execute(delete(Strategy).where(Strategy.id == strategy_id))
    await session.commit()
    return result.rowcount > 0


async def get_inflight_strategy_tokens(session: AsyncSession, user_id: UUID) -> set:
    """
    Token IDs that already have a strategy opportunity in flight (queued/executing).

    Used for anti-thrash idempotency: don't emit a second intent for a token whose
    prior intent hasn't resolved yet — otherwise a flip-flopping strategy could
    stack duplicate open/close orders on the same position across ticks.
    """
    result = await session.execute(
        select(Opportunity.outcome_prices).where(and_(
            Opportunity.user_id == user_id,
            Opportunity.strategy_type == "strategy",
            Opportunity.status.in_(["queued", "executing"]),
        ))
    )
    tokens = set()
    for (payload,) in result.all():
        if isinstance(payload, dict) and payload.get("token_id"):
            tokens.add(payload["token_id"])
    return tokens


async def get_open_positions(session: AsyncSession, user_id: UUID, strategy_id: Optional[UUID] = None) -> dict:
    """
    Aggregate strategy trades into net positions per token_id.

    Uses average-cost accounting over BUY fills; SELL fills reduce net quantity.
    Returns {token_id: {"qty": float, "avg_entry_price": float}} for tokens with
    a positive net quantity. Only strategy_type='strategy' trades are counted.
    """
    q = select(Trade).where(and_(Trade.user_id == user_id, Trade.strategy_type == "strategy"))
    if strategy_id is not None:
        q = q.where(Trade.strategy_id == strategy_id)
    q = q.order_by(Trade.executed_at)
    result = await session.execute(q)
    trades = list(result.scalars().all())

    acc: dict = {}  # token_id -> {"qty", "cost"}
    for t in trades:
        tid = t.token_id
        if not tid:
            continue
        filled = float(t.filled_size or 0)
        price = float(t.fill_price or 0)
        entry = acc.setdefault(tid, {"qty": 0.0, "cost": 0.0})
        if filled >= 0:  # buy
            entry["qty"] += filled
            entry["cost"] += filled * price
        else:  # sell reduces position at average cost
            avg = entry["cost"] / entry["qty"] if entry["qty"] > 0 else 0.0
            sell_qty = min(-filled, entry["qty"])
            entry["qty"] -= sell_qty
            entry["cost"] -= sell_qty * avg

    positions = {}
    for tid, e in acc.items():
        if e["qty"] > 1e-9:
            positions[tid] = {
                "qty": e["qty"],
                "avg_entry_price": e["cost"] / e["qty"] if e["qty"] > 0 else 0.0,
            }
    return positions
