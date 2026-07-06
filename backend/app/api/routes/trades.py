from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone, timedelta
from typing import Optional
import csv
import io

from app.database.session import get_db
from app.database.schema import User
from app.database import queries
from app.api.auth import get_current_user
from app.models.trade import TradeResponse, TradeSummaryResponse

router = APIRouter(prefix="/trades", tags=["trades"])


@router.get("", response_model=list[TradeResponse])
async def list_trades(
    strategy_type: Optional[str] = None,
    status: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    trades = await queries.get_trades(
        db, user.id,
        strategy_type=strategy_type,
        status=status,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )
    return trades


@router.get("/summary", response_model=TradeSummaryResponse)
async def trade_summary(
    period: str = Query(default="all", pattern="^(today|week|month|all)$"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    since = None
    now = datetime.now(timezone.utc)
    if period == "today":
        since = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        since = now - timedelta(days=7)
    elif period == "month":
        since = now - timedelta(days=30)

    summary = await queries.get_trade_summary(db, user.id, since=since)
    return TradeSummaryResponse(**summary)


@router.get("/export")
async def export_trades_csv(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    trades = await queries.get_trades(
        db, user.id, start_date=start_date, end_date=end_date, limit=10000
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "timestamp", "market_id", "strategy_type", "side",
        "size_usdc", "fill_price", "fees_paid", "profit_loss",
        "status", "is_paper", "tx_hash",
    ])
    for t in trades:
        writer.writerow([
            t.executed_at, t.market_id, t.strategy_type, t.side,
            t.size_usdc, t.fill_price, t.fees_paid, t.profit_loss,
            t.status, t.is_paper, t.tx_hash,
        ])

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=trades.csv"},
    )
