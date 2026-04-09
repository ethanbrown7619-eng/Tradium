from pydantic import BaseModel
from uuid import UUID
from datetime import datetime
from typing import Optional
from decimal import Decimal


class TradeResponse(BaseModel):
    id: UUID
    user_id: UUID
    opportunity_id: Optional[UUID] = None
    market_id: str
    condition_id: Optional[str] = None
    strategy_type: str
    side: str
    size_usdc: float
    fill_price: Optional[float] = None
    filled_size: Optional[float] = None
    fees_paid: Optional[float] = None
    profit_loss: Optional[float] = None
    status: str
    order_id: Optional[str] = None
    tx_hash: Optional[str] = None
    is_paper: bool = False
    executed_at: datetime
    price_snapshot: Optional[dict] = None

    class Config:
        from_attributes = True


class TradeSummaryResponse(BaseModel):
    total_trades: int
    total_pnl: float
    total_fees: float
    total_volume: float
    winning_trades: int
    win_rate: float


class TradeHistoryParams(BaseModel):
    strategy_type: Optional[str] = None
    status: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    limit: int = 50
    offset: int = 0
