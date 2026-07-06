from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime, ForeignKey,
    Text, Numeric, JSON, Index, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, relationship
import uuid


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    config = relationship("UserConfig", back_populates="user", uselist=False, cascade="all, delete-orphan")
    opportunities = relationship("Opportunity", back_populates="user", cascade="all, delete-orphan")
    trades = relationship("Trade", back_populates="user", cascade="all, delete-orphan")


class UserConfig(Base):
    __tablename__ = "user_config"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)

    # Encrypted wallet key
    encrypted_private_key = Column(Text, nullable=True)
    wallet_address = Column(String(42), nullable=True)

    # Trading settings
    settings = Column(JSONB, nullable=False, default=lambda: {
        "bot_active": False,
        "paper_trading": True,
        "min_profit_pct": 1.0,
        "min_profit_usdc": 0.50,
        "max_trade_size": 100.0,
        "max_capital_deployed": 1000.0,
        "min_liquidity": 500.0,
        "auto_execute_binary": True,
        "auto_execute_multi": False,
        "scan_interval": 15,
        "excluded_markets": [],
        "excluded_keywords": [],
        "categories": ["all"],
        "usdc_budget": 1000.0,
        "reserve_amount": 50.0,
        "max_daily_loss": 50.0,
        "max_trades_per_hour": 20,
        "cooldown_after_fail": 60,
        "notify_email": None,
        "notify_on_trade": True,
        "notify_on_opportunity": False,
        "notify_on_daily_summary": True,
        "notify_on_error": True,
        "notify_on_kill_switch": True,
    })

    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="config")


class Opportunity(Base):
    __tablename__ = "opportunities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    market_id = Column(String(255), nullable=False, index=True)
    condition_id = Column(String(255), nullable=True)
    market_slug = Column(String(500), nullable=True)
    market_question = Column(Text, nullable=True)

    strategy_type = Column(String(50), nullable=False)  # binary, multi_outcome, correlated
    outcome_prices = Column(JSONB, nullable=False)  # {"YES": 0.45, "NO": 0.52} or {"A": 0.40, ...}
    token_ids = Column(JSONB, nullable=True)  # {"YES": "0x..", "NO": "0x.."} real CLOB token IDs
    price_sum = Column(Numeric(10, 6), nullable=False)
    estimated_profit_pct = Column(Numeric(10, 6), nullable=False)
    estimated_profit_usdc = Column(Numeric(10, 4), nullable=True)
    liquidity_depth = Column(Numeric(12, 2), nullable=True)
    confidence_score = Column(Numeric(5, 4), nullable=True)  # For correlated strategy

    status = Column(String(50), nullable=False, default="pending")
    # Status values: pending, queued, executing, executed, failed, expired, flagged

    found_at = Column(DateTime(timezone=True), server_default=func.now())
    executed_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="opportunities")
    trades = relationship("Trade", back_populates="opportunity", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_opportunities_status_found", "status", "found_at"),
    )


class Trade(Base):
    __tablename__ = "trades"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    opportunity_id = Column(UUID(as_uuid=True), ForeignKey("opportunities.id", ondelete="SET NULL"), nullable=True)
    market_id = Column(String(255), nullable=False)
    condition_id = Column(String(255), nullable=True)
    token_id = Column(String(255), nullable=True, index=True)  # for strategy position aggregation
    strategy_id = Column(UUID(as_uuid=True), nullable=True, index=True)  # links to strategies.id

    strategy_type = Column(String(50), nullable=False)
    side = Column(String(50), nullable=False)  # YES/NO, outcome name, or "buy:YES"/"sell:NO"
    size_usdc = Column(Numeric(12, 4), nullable=False)
    fill_price = Column(Numeric(10, 6), nullable=True)
    filled_size = Column(Numeric(12, 4), nullable=True)
    fees_paid = Column(Numeric(10, 4), nullable=True)
    profit_loss = Column(Numeric(12, 4), nullable=True)

    status = Column(String(50), nullable=False, default="pending")
    # Status values: pending, submitted, filled, partial, cancelled, failed

    order_id = Column(String(255), nullable=True)
    tx_hash = Column(String(66), nullable=True)
    is_paper = Column(Boolean, default=False)

    executed_at = Column(DateTime(timezone=True), server_default=func.now())
    settled_at = Column(DateTime(timezone=True), nullable=True)

    # Snapshot of prices at execution time for audit
    price_snapshot = Column(JSONB, nullable=True)

    user = relationship("User", back_populates="trades")
    opportunity = relationship("Opportunity", back_populates="trades")

    __table_args__ = (
        Index("ix_trades_user_executed", "user_id", "executed_at"),
    )


class MarketCache(Base):
    __tablename__ = "markets_cache"

    market_id = Column(String(255), primary_key=True)
    condition_id = Column(String(255), nullable=True, index=True)
    data = Column(JSONB, nullable=False)
    cached_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_markets_cache_cached_at", "cached_at"),
    )


class Strategy(Base):
    __tablename__ = "strategies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    enabled = Column(Boolean, default=False)

    # The full validated StrategyDefinition (universe, entry/exit/stop AST, sizing,
    # order spec, cooldown, max_open_positions). Validated at save time against the
    # engine's indicator whitelist — never executed as code, only interpreted.
    definition = Column(JSONB, nullable=False)

    last_triggered_at = Column(DateTime(timezone=True), nullable=True)  # cooldown tracking
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_strategies_user_enabled", "user_id", "enabled"),
    )
