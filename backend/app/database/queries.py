from sqlalchemy import select, update, delete, func, and_, or_, desc
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta, timezone
from uuid import UUID
from typing import Optional
from decimal import Decimal

from app.database.schema import User, UserConfig, Opportunity, Trade, MarketCache


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
