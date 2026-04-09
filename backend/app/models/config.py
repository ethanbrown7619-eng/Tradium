from pydantic import BaseModel, Field
from typing import Optional
from uuid import UUID


class TradingSettings(BaseModel):
    bot_active: bool = False
    paper_trading: bool = True
    min_profit_pct: float = Field(default=1.0, ge=0)
    min_profit_usdc: float = Field(default=0.50, ge=0)
    max_trade_size: float = Field(default=100.0, ge=1)
    max_capital_deployed: float = Field(default=1000.0, ge=1)
    min_liquidity: float = Field(default=500.0, ge=0)
    auto_execute_binary: bool = True
    auto_execute_multi: bool = False
    scan_interval: int = Field(default=15, ge=5, le=300)
    excluded_markets: list[str] = []
    excluded_keywords: list[str] = []
    categories: list[str] = ["all"]
    usdc_budget: float = Field(default=1000.0, ge=0)
    reserve_amount: float = Field(default=50.0, ge=0)
    max_daily_loss: float = Field(default=50.0, ge=0)
    max_trades_per_hour: int = Field(default=20, ge=1, le=100)
    cooldown_after_fail: int = Field(default=60, ge=0)
    notify_email: Optional[str] = None
    notify_on_trade: bool = True
    notify_on_opportunity: bool = False
    notify_on_daily_summary: bool = True
    notify_on_error: bool = True
    notify_on_kill_switch: bool = True


class ConfigResponse(BaseModel):
    user_id: UUID
    settings: TradingSettings
    wallet_address: Optional[str] = None
    has_private_key: bool = False
    updated_at: Optional[str] = None


class WalletSetup(BaseModel):
    private_key: str


class KillSwitchRequest(BaseModel):
    activate: bool
