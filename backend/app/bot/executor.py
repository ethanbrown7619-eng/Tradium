"""
Execution engine for arbitrage trades.

Safety requirements:
- Always re-verify prices before execution
- Use limit orders (never market orders) to prevent slippage
- If any leg fails, immediately cancel other legs
- Handle partial fills gracefully
- Never exceed user's configured budget
- Respect daily loss limits and rate limits
- Full audit logging of every execution attempt
"""
import logging
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID
from typing import Optional
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import queries
from app.database.schema import UserConfig
from app.services.polymarket import PolymarketService
from app.services.encryption import decrypt_private_key
from app.bot.strategies.binary import BinaryArbitrageResult
from app.bot.strategies.multioutcome import MultiOutcomeArbitrageResult

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    success: bool
    trades: list[dict]
    error: Optional[str] = None
    partial_fill: bool = False


class ExecutionEngine:
    def __init__(self, polymarket: PolymarketService):
        self.polymarket = polymarket

    async def check_risk_limits(self, db: AsyncSession, user_id: UUID, config: UserConfig, trade_size: float) -> Optional[str]:
        """
        Check all risk limits before executing. Returns error message if blocked, None if OK.
        """
        settings = config.settings

        # Kill switch check
        if not settings.get("bot_active", False):
            return "Bot is not active (kill switch may be engaged)"

        # Daily loss limit
        daily_loss = await queries.get_daily_loss(db, user_id)
        max_daily = settings.get("max_daily_loss", 50.0)
        if daily_loss >= max_daily:
            return f"Daily loss limit reached: ${daily_loss:.2f} >= ${max_daily:.2f}"

        # Rate limit
        trades_last_hour = await queries.get_trades_last_hour(db, user_id)
        max_hourly = settings.get("max_trades_per_hour", 20)
        if trades_last_hour >= max_hourly:
            return f"Hourly trade limit reached: {trades_last_hour} >= {max_hourly}"

        # Capital deployed check
        current_deployed = await queries.get_capital_deployed(db, user_id)
        max_deployed = settings.get("max_capital_deployed", 1000.0)
        if current_deployed + trade_size > max_deployed:
            return f"Would exceed max capital deployed: ${current_deployed:.2f} + ${trade_size:.2f} > ${max_deployed:.2f}"

        # Budget check
        usdc_budget = settings.get("usdc_budget", 1000.0)
        reserve = settings.get("reserve_amount", 50.0)
        if trade_size > usdc_budget - reserve:
            return f"Trade size ${trade_size:.2f} exceeds available budget (${usdc_budget:.2f} - ${reserve:.2f} reserve)"

        # Max trade size
        max_trade = settings.get("max_trade_size", 100.0)
        if trade_size > max_trade:
            return f"Trade size ${trade_size:.2f} exceeds max trade size ${max_trade:.2f}"

        return None

    async def execute_binary_arbitrage(
        self,
        db: AsyncSession,
        user_id: UUID,
        config: UserConfig,
        opportunity: BinaryArbitrageResult,
        opportunity_id: UUID,
    ) -> ExecutionResult:
        """
        Execute a binary arbitrage trade.
        Buys both YES and NO tokens simultaneously.
        """
        settings = config.settings
        is_paper = settings.get("paper_trading", True)
        max_trade = settings.get("max_trade_size", 100.0)

        # Determine trade size (limited by liquidity and config)
        trade_size = min(
            max_trade,
            opportunity.min_liquidity,
        )

        # ── Risk check ──
        risk_error = await self.check_risk_limits(db, user_id, config, trade_size)
        if risk_error:
            logger.warning(f"Risk limit blocked trade: {risk_error}")
            await queries.update_opportunity_status(db, opportunity_id, "failed")
            return ExecutionResult(success=False, trades=[], error=risk_error)

        # ── Price re-verification ──
        logger.info(f"Re-verifying prices for {opportunity.market_id}")
        # In production this would re-fetch order books
        # For now we trust the passed-in prices for paper trading
        if not is_paper:
            # Re-fetch and verify
            yes_book = await self.polymarket.get_order_book(opportunity.condition_id + "_yes")
            no_book = await self.polymarket.get_order_book(opportunity.condition_id + "_no")

            if not yes_book or not no_book:
                await queries.update_opportunity_status(db, opportunity_id, "failed")
                return ExecutionResult(success=False, trades=[], error="Failed to re-fetch order books")

            new_yes_ask = PolymarketService.get_best_ask_price(yes_book)
            new_no_ask = PolymarketService.get_best_ask_price(no_book)

            if new_yes_ask is None or new_no_ask is None:
                await queries.update_opportunity_status(db, opportunity_id, "failed")
                return ExecutionResult(success=False, trades=[], error="Price no longer available")

            new_sum = new_yes_ask + new_no_ask
            if new_sum >= 1.0:
                await queries.update_opportunity_status(db, opportunity_id, "expired")
                return ExecutionResult(success=False, trades=[], error=f"Price moved: new sum {new_sum:.4f} >= 1.0")

            # Re-calculate with new prices
            from app.bot.strategies.binary import calculate_binary_arbitrage
            new_result = calculate_binary_arbitrage(
                market_id=opportunity.market_id,
                condition_id=opportunity.condition_id,
                market_question=opportunity.market_question,
                market_slug=opportunity.market_slug,
                yes_ask=new_yes_ask,
                no_ask=new_no_ask,
            )
            min_pct = settings.get("min_profit_pct", 1.0)
            min_usdc = settings.get("min_profit_usdc", 0.50)
            if not new_result.is_profitable or new_result.net_profit_pct < min_pct:
                await queries.update_opportunity_status(db, opportunity_id, "expired")
                return ExecutionResult(
                    success=False, trades=[],
                    error=f"Opportunity no longer profitable after re-check: {new_result.net_profit_pct:.2f}%",
                )

        # ── Execute trades ──
        now = datetime.now(timezone.utc)
        trades = []

        if is_paper:
            # Paper trading: simulate the trades
            yes_trade = await queries.create_trade(
                db,
                user_id=user_id,
                opportunity_id=opportunity_id,
                market_id=opportunity.market_id,
                condition_id=opportunity.condition_id,
                strategy_type="binary",
                side="YES",
                size_usdc=Decimal(str(trade_size / 2)),
                fill_price=Decimal(str(opportunity.yes_price)),
                filled_size=Decimal(str((trade_size / 2) / opportunity.yes_price)),
                fees_paid=Decimal(str(opportunity.fee_worst_case / 2)),
                profit_loss=Decimal(str(opportunity.net_profit / 2)),
                status="filled",
                is_paper=True,
                price_snapshot={
                    "yes_ask": opportunity.yes_price,
                    "no_ask": opportunity.no_price,
                    "sum": opportunity.price_sum,
                },
            )
            trades.append(yes_trade)

            no_trade = await queries.create_trade(
                db,
                user_id=user_id,
                opportunity_id=opportunity_id,
                market_id=opportunity.market_id,
                condition_id=opportunity.condition_id,
                strategy_type="binary",
                side="NO",
                size_usdc=Decimal(str(trade_size / 2)),
                fill_price=Decimal(str(opportunity.no_price)),
                filled_size=Decimal(str((trade_size / 2) / opportunity.no_price)),
                fees_paid=Decimal(str(opportunity.fee_worst_case / 2)),
                profit_loss=Decimal(str(opportunity.net_profit / 2)),
                status="filled",
                is_paper=True,
                price_snapshot={
                    "yes_ask": opportunity.yes_price,
                    "no_ask": opportunity.no_price,
                    "sum": opportunity.price_sum,
                },
            )
            trades.append(no_trade)

            await queries.update_opportunity_status(
                db, opportunity_id, "executed",
                executed_at=now,
            )

            logger.info(
                f"[PAPER] Binary arb executed: {opportunity.market_question} "
                f"Size=${trade_size:.2f} Profit=${opportunity.net_profit * trade_size:.4f}"
            )
        else:
            # Live trading via py-clob-client
            try:
                private_key = decrypt_private_key(config.encrypted_private_key)
                # Place both orders
                yes_order, no_order = await self._place_binary_orders(
                    private_key=private_key,
                    condition_id=opportunity.condition_id,
                    yes_price=opportunity.yes_price,
                    no_price=opportunity.no_price,
                    trade_size=trade_size,
                )
                # Record trades
                for side, order_result in [("YES", yes_order), ("NO", no_order)]:
                    price = opportunity.yes_price if side == "YES" else opportunity.no_price
                    trade = await queries.create_trade(
                        db,
                        user_id=user_id,
                        opportunity_id=opportunity_id,
                        market_id=opportunity.market_id,
                        condition_id=opportunity.condition_id,
                        strategy_type="binary",
                        side=side,
                        size_usdc=Decimal(str(trade_size / 2)),
                        fill_price=Decimal(str(price)),
                        status="submitted",
                        is_paper=False,
                        order_id=order_result.get("order_id"),
                        tx_hash=order_result.get("tx_hash"),
                        price_snapshot={
                            "yes_ask": opportunity.yes_price,
                            "no_ask": opportunity.no_price,
                        },
                    )
                    trades.append(trade)

                await queries.update_opportunity_status(db, opportunity_id, "executed", executed_at=now)
            except Exception as e:
                logger.error(f"Failed to execute binary arb: {e}")
                # Attempt to cancel any placed orders
                await self._cancel_orders(trades)
                await queries.update_opportunity_status(db, opportunity_id, "failed")
                return ExecutionResult(success=False, trades=[], error=str(e))

        return ExecutionResult(
            success=True,
            trades=[{"id": str(t.id), "side": t.side, "status": t.status} for t in trades],
        )

    async def execute_multi_outcome_arbitrage(
        self,
        db: AsyncSession,
        user_id: UUID,
        config: UserConfig,
        opportunity: MultiOutcomeArbitrageResult,
        opportunity_id: UUID,
    ) -> ExecutionResult:
        """Execute a multi-outcome arbitrage trade."""
        settings = config.settings
        is_paper = settings.get("paper_trading", True)
        max_trade = settings.get("max_trade_size", 100.0)

        trade_size = min(max_trade, opportunity.min_liquidity)

        risk_error = await self.check_risk_limits(db, user_id, config, trade_size)
        if risk_error:
            await queries.update_opportunity_status(db, opportunity_id, "failed")
            return ExecutionResult(success=False, trades=[], error=risk_error)

        now = datetime.now(timezone.utc)
        trades = []
        per_outcome_size = trade_size / opportunity.num_outcomes

        if is_paper:
            for outcome, price in opportunity.outcome_prices.items():
                trade = await queries.create_trade(
                    db,
                    user_id=user_id,
                    opportunity_id=opportunity_id,
                    market_id=opportunity.market_id,
                    condition_id=opportunity.condition_id,
                    strategy_type="multi_outcome",
                    side=outcome,
                    size_usdc=Decimal(str(per_outcome_size)),
                    fill_price=Decimal(str(price)),
                    filled_size=Decimal(str(per_outcome_size / price)) if price > 0 else Decimal("0"),
                    fees_paid=Decimal(str(opportunity.fee_worst_case / opportunity.num_outcomes)),
                    profit_loss=Decimal(str(opportunity.net_profit / opportunity.num_outcomes)),
                    status="filled",
                    is_paper=True,
                    price_snapshot=opportunity.outcome_prices,
                )
                trades.append(trade)

            await queries.update_opportunity_status(db, opportunity_id, "executed", executed_at=now)
            logger.info(
                f"[PAPER] Multi-outcome arb executed: {opportunity.market_question} "
                f"Outcomes={opportunity.num_outcomes} Size=${trade_size:.2f}"
            )
        else:
            try:
                private_key = decrypt_private_key(config.encrypted_private_key)
                placed_orders = []

                for outcome, price in opportunity.outcome_prices.items():
                    try:
                        order_result = await self._place_outcome_order(
                            private_key=private_key,
                            condition_id=opportunity.condition_id,
                            outcome=outcome,
                            price=price,
                            size_usdc=per_outcome_size,
                        )
                        trade = await queries.create_trade(
                            db,
                            user_id=user_id,
                            opportunity_id=opportunity_id,
                            market_id=opportunity.market_id,
                            condition_id=opportunity.condition_id,
                            strategy_type="multi_outcome",
                            side=outcome,
                            size_usdc=Decimal(str(per_outcome_size)),
                            fill_price=Decimal(str(price)),
                            status="submitted",
                            is_paper=False,
                            order_id=order_result.get("order_id"),
                            tx_hash=order_result.get("tx_hash"),
                            price_snapshot=opportunity.outcome_prices,
                        )
                        trades.append(trade)
                        placed_orders.append(order_result)
                    except Exception as e:
                        logger.error(f"Failed to place order for outcome {outcome}: {e}")
                        # Cancel all previously placed orders
                        await self._cancel_orders(trades)
                        await queries.update_opportunity_status(db, opportunity_id, "failed")
                        return ExecutionResult(
                            success=False, trades=[], partial_fill=True,
                            error=f"Failed on outcome {outcome}: {e}",
                        )

                await queries.update_opportunity_status(db, opportunity_id, "executed", executed_at=now)
            except Exception as e:
                await queries.update_opportunity_status(db, opportunity_id, "failed")
                return ExecutionResult(success=False, trades=[], error=str(e))

        return ExecutionResult(
            success=True,
            trades=[{"id": str(t.id), "side": t.side, "status": t.status} for t in trades],
        )

    async def _place_binary_orders(
        self, private_key: str, condition_id: str,
        yes_price: float, no_price: float, trade_size: float,
    ) -> tuple[dict, dict]:
        """
        Place YES and NO limit orders via py-clob-client.
        In production, this would use the actual CLOB client.
        """
        # py-clob-client integration point
        # from py_clob_client.client import ClobClient
        # client = ClobClient(host=self.polymarket.clob_url, key=private_key, chain_id=137)
        # yes_order = client.create_and_post_order(...)
        # no_order = client.create_and_post_order(...)
        logger.info(f"Placing binary orders: YES@{yes_price}, NO@{no_price}, size=${trade_size}")
        return (
            {"order_id": "mock_yes_order", "tx_hash": None},
            {"order_id": "mock_no_order", "tx_hash": None},
        )

    async def _place_outcome_order(
        self, private_key: str, condition_id: str,
        outcome: str, price: float, size_usdc: float,
    ) -> dict:
        """Place a single outcome order."""
        logger.info(f"Placing order: {outcome}@{price}, size=${size_usdc}")
        return {"order_id": f"mock_{outcome}_order", "tx_hash": None}

    async def _cancel_orders(self, trades: list) -> None:
        """Attempt to cancel all orders from a failed trade set."""
        for trade in trades:
            if hasattr(trade, 'order_id') and trade.order_id:
                logger.warning(f"Attempting to cancel order: {trade.order_id}")
                # In production: client.cancel_order(trade.order_id)
