from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.database.schema import User
from app.database import queries
from app.api.auth import get_current_user
from app.services.encryption import decrypt_private_key

router = APIRouter(prefix="/wallet", tags=["wallet"])


@router.get("/balance")
async def get_wallet_balance(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    config = await queries.get_user_config(db, user.id)
    if not config or not config.wallet_address:
        return {"balance": None, "address": None, "error": "No wallet configured"}

    try:
        from app.services.polymarket import PolymarketService
        service = PolymarketService()
        balance = await service.get_usdc_balance(config.wallet_address)
        return {
            "address": config.wallet_address,
            "usdc_balance": balance,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch balance: {str(e)}")


@router.delete("")
async def disconnect_wallet(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await queries.update_user_config(
        db, user.id,
        encrypted_private_key=None,
        wallet_address=None,
    )
    return {"status": "wallet disconnected"}
