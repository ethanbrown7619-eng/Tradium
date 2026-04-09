from pydantic import BaseModel
from uuid import UUID
from datetime import datetime
from typing import Optional


class OpportunityResponse(BaseModel):
    id: UUID
    user_id: UUID
    market_id: str
    condition_id: Optional[str] = None
    market_slug: Optional[str] = None
    market_question: Optional[str] = None
    strategy_type: str
    outcome_prices: dict
    price_sum: float
    estimated_profit_pct: float
    estimated_profit_usdc: Optional[float] = None
    liquidity_depth: Optional[float] = None
    confidence_score: Optional[float] = None
    status: str
    found_at: datetime
    executed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class OpportunityAction(BaseModel):
    action: str  # "approve" or "reject"
