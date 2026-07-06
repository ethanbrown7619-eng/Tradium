"""
Strategy CRUD endpoints (Strategy mode — goal B).

Because the request body is a StrategyDefinition, FastAPI runs the Pydantic
validators on every create/update — so a strategy referencing an unknown
indicator or malformed condition is rejected with HTTP 422 before it is ever
stored. There is no path by which an invalid (or code-bearing) strategy persists.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.database.session import get_db
from app.database.schema import User
from app.database import queries
from app.api.auth import get_current_user
from app.models.strategy import StrategyDefinition, StrategyResponse

router = APIRouter(prefix="/strategies", tags=["strategies"])


@router.get("", response_model=list[StrategyResponse])
async def list_strategies(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await queries.get_strategies(db, user.id)


@router.post("", response_model=StrategyResponse, status_code=201)
async def create_strategy(
    body: StrategyDefinition,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    strat = await queries.create_strategy(
        db, user.id, name=body.name, definition=body.model_dump(), enabled=body.enabled
    )
    return strat


@router.post("/validate")
async def validate_strategy(body: StrategyDefinition):
    """Dry-run: if this returns 200 the strategy is well-formed and safe to store."""
    return {"valid": True, "name": body.name}


@router.get("/{strategy_id}", response_model=StrategyResponse)
async def get_strategy(
    strategy_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    strat = await queries.get_strategy(db, strategy_id)
    if not strat or strat.user_id != user.id:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return strat


@router.put("/{strategy_id}", response_model=StrategyResponse)
async def update_strategy(
    strategy_id: UUID,
    body: StrategyDefinition,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    strat = await queries.get_strategy(db, strategy_id)
    if not strat or strat.user_id != user.id:
        raise HTTPException(status_code=404, detail="Strategy not found")
    updated = await queries.update_strategy(
        db, strategy_id, name=body.name, definition=body.model_dump(), enabled=body.enabled
    )
    return updated


@router.delete("/{strategy_id}")
async def delete_strategy(
    strategy_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    strat = await queries.get_strategy(db, strategy_id)
    if not strat or strat.user_id != user.id:
        raise HTTPException(status_code=404, detail="Strategy not found")
    await queries.delete_strategy(db, strategy_id)
    return {"status": "deleted"}
