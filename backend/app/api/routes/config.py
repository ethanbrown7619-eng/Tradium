from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.database.schema import User
from app.database import queries
from app.api.auth import get_current_user
from app.models.config import TradingSettings, ConfigResponse, WalletSetup, KillSwitchRequest
from app.services.encryption import encrypt_private_key

router = APIRouter(prefix="/config", tags=["config"])


@router.get("", response_model=ConfigResponse)
async def get_config(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    config = await queries.get_user_config(db, user.id)
    if not config:
        raise HTTPException(status_code=404, detail="Config not found")
    return ConfigResponse(
        user_id=user.id,
        settings=TradingSettings(**config.settings),
        wallet_address=config.wallet_address,
        has_private_key=config.encrypted_private_key is not None,
        updated_at=str(config.updated_at) if config.updated_at else None,
    )


@router.put("", response_model=ConfigResponse)
async def update_config(
    body: TradingSettings,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    config = await queries.update_user_config(db, user.id, settings=body.model_dump())
    return ConfigResponse(
        user_id=user.id,
        settings=TradingSettings(**config.settings),
        wallet_address=config.wallet_address,
        has_private_key=config.encrypted_private_key is not None,
        updated_at=str(config.updated_at) if config.updated_at else None,
    )


@router.post("/wallet")
async def setup_wallet(
    body: WalletSetup,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Derive address from private key
    try:
        from eth_account import Account
        account = Account.from_key(body.private_key)
        address = account.address
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid private key")

    encrypted = encrypt_private_key(body.private_key)
    await queries.update_user_config(
        db, user.id,
        encrypted_private_key=encrypted,
        wallet_address=address,
    )
    return {"wallet_address": address, "status": "connected"}


@router.post("/kill-switch")
async def kill_switch(
    body: KillSwitchRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    config = await queries.get_user_config(db, user.id)
    if not config:
        raise HTTPException(status_code=404, detail="Config not found")

    settings = dict(config.settings)
    if body.activate:
        settings["bot_active"] = False
    else:
        settings["bot_active"] = True

    await queries.update_user_config(db, user.id, settings=settings)
    return {"bot_active": not body.activate, "kill_switch_activated": body.activate}
