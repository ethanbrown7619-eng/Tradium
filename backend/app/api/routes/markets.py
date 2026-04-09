from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.database.session import get_db
from app.database.schema import User
from app.database import queries
from app.api.auth import get_current_user
from app.models.opportunity import OpportunityResponse, OpportunityAction
from app.services.polymarket import PolymarketService

router = APIRouter(prefix="/markets", tags=["markets"])
polymarket_service = PolymarketService()


@router.get("")
async def list_markets(
    search: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Browse all active Polymarket markets."""
    markets = await polymarket_service.get_markets(
        search=search, category=category, limit=limit, offset=offset
    )
    return markets


@router.get("/{market_id}")
async def get_market_detail(market_id: str):
    """Get detailed info for a specific market including order book."""
    market = await polymarket_service.get_market(market_id)
    if not market:
        return {"error": "Market not found"}
    order_book = await polymarket_service.get_order_book(market.get("condition_id", market_id))
    return {"market": market, "order_book": order_book}


@router.get("/opportunities", response_model=list[OpportunityResponse])
async def list_opportunities(
    status: Optional[str] = None,
    strategy_type: Optional[str] = None,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    opps = await queries.get_opportunities(
        db, user.id, status=status, strategy_type=strategy_type,
        limit=limit, offset=offset,
    )
    return opps


@router.post("/opportunities/{opp_id}/action")
async def opportunity_action(
    opp_id: str,
    body: OpportunityAction,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from uuid import UUID
    opp = await queries.update_opportunity_status(
        db, UUID(opp_id),
        status="queued" if body.action == "approve" else "rejected",
    )
    if not opp:
        return {"error": "Opportunity not found"}
    return {"status": opp.status}
