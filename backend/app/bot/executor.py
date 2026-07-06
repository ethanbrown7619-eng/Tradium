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

SIZING INVARIANT (critical):
An arbitrage hedge is only risk-free if you hold an EQUAL NUMBER OF SHARES on
every leg. At settlement exactly one leg pays $1.00/share, so holding N shares of
each leg returns exactly N dollars regardless of which outcome wins. Splitting
capital evenly by DOLLARS instead of shares produces unequal share counts and
turns a "guaranteed" arb into a position that loses money whenever the expensive
leg wins. All sizing here goes through compute_leg_sizes to enforce equal shares.
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
from app.bot.strategies.binary import BinaryArbitrageResult, FEE_RATE
from app.bot.strategies.multioutcome import MultiOutcomeArbitrageResult

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    success: bool
    trades: list[dict]
    error: Optional[str] = None
    partial_fill: bool = False


def compute_leg_sizes(prices: dict[str, float], target_notional: float) -> tuple[float, dict[str, float]]:
    """
    Compute the number of SHARES to buy on each leg of an arbitrage so that all
    legs hold an equal share count and total cost is ~= target_notional.

    prices: {outcome_name: per_share_ask_price}
    target_notional: total USDC we want to deploy across all legs

    Returns (shares, per_leg_cost) where:
      shares            = equal number of shares bought on EVERY leg
      per_leg_cost[o]   = shares * prices[o]  (USDC cost of that leg)
      sum(per_leg_cost) = shares * sum(prices) ~= target_notional
    """
    price_sum = sum(prices.values())
    if price_sum <= 0 or target_notional <= 0:
        return 0.0, {k: 0.0 for k in prices}
    shares = target_notional / price_sum
    per_leg_cost = {k: shares * p for k, p in prices.items()}
    return shares, per_leg_cost


def _arb_pnl(shares: float, price_sum: float, fee_worst_case_per_share: float, gas_cost: float) -> tuple[float, float, float]:
    """
    Compute dollar-denominated economics of an equal-share arbitrage position.

    Returns (total_cost, total_fee, total_net_profit), all in USDC:
      total_cost       = shares * price_sum
      total_gross      = shares * (1 - price_sum)   (payout of N shares minus cost)
      total_fee        = shares * fee_worst_case_per_share
      total_net_profit = total_gross - total_fee - gas_cost
    """
    total_cost = shares * price_sum
    total_gross = shares * (1.0 - price_sum)
    total_fee = shares * fee_worst_case_per_share
    total_net_profit = total_gross - total_fee - gas_cost
    return total_cost, total_fee, total_net_profit


class ExecutionEngine:
    def __init__(self, polymarket: PolymarketService):
        self.polymarket = polymarket

    async def check_risk_limits(self, db: AsyncSession, user_id: UUID, config: UserConfig, trade_size: float) -> Optional[str]:
        """
        Check all risk limits before executing. Returns error message if blocked, None if OK.
        `trade_size` is the total USDC that will be deployed across all legs.
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
        Buys an EQUAL NUMBER OF SHARES of YES and NO so the position is a true hedge.
        """
        settings = config.settings
        is_paper = settings.get("paper_trading", True)
        max_trade = settings.get("max_trade_size", 100.0)

        # ── Atomic claim: only one caller may execute a given opportunity ──
        claimed = await queries.claim_opportunity(db, opportunity_id, "queued", "executing")
        if not claimed:
            logger.info(f"Opportunity {opportunity_id} already claimed/executing; skipping")
            return ExecutionResult(success=False, trades=[], error="Opportunity already claimed")

        # Target notional to deploy (capped by config and available liquidity depth)
        target_notional = min(max_trade, opportunity.min_liquidity)

        # Equal-share sizing across both legs
        prices = {"YES": opportunity.yes_price, "NO": opportunity.no_price}
        shares, per_leg_cost = compute_leg_sizes(prices, target_notional)
        total_cost, total_fee, total_net_profit = _arb_pnl(
            shares, opportunity.price_sum, opportunity.fee_worst_case, opportunity.gas_cost
        )

        if shares <= 0:
            await queries.update_opportunity_status(db, opportunity_id, "failed")
            return ExecutionResult(success=False, trades=[], error="Computed zero share size")

        # ── Risk check (against the actual total cost being deployed) ──
        risk_error = await self.check_risk_limits(db, user_id, config, total_cost)
        if risk_error:
            logger.warning(f"Risk limit blocked trade: {risk_error}")
            await queries.update_opportunity_status(db, opportunity_id, "failed")
            return ExecutionResult(success=False, trades=[], error=risk_error)

        # ── Price re-verification (live only) ──
        if not is_paper:
            verify_error = await self._reverify_binary(db, settings, opportunity, opportunity_id)
            if verify_error:
                return ExecutionResult(success=False, trades=[], error=verify_error)

        now = datetime.now(timezone.utc)
        trades = []
        num_legs = len(prices)
        # Allocate the worst-case fee and net profit evenly across legs for recording
        fee_per_leg = total_fee / num_legs
        pnl_per_leg = total_net_profit / num_legs

        if is_paper:
            for side, price in prices.items():
                trade = await queries.create_trade(
                    db,
                    user_id=user_id,
                    opportunity_id=opportunity_id,
                    market_id=opportunity.market_id,
                    condition_id=opportunity.condition_id,
                    strategy_type="binary",
                    side=side,
                    size_usdc=Decimal(str(per_leg_cost[side])),
                    fill_price=Decimal(str(price)),
                    filled_size=Decimal(str(shares)),  # equal shares on every leg
                    fees_paid=Decimal(str(fee_per_leg)),
                    profit_loss=Decimal(str(pnl_per_leg)),
                    status="filled",
                    is_paper=True,
                    price_snapshot={
                        "yes_ask": opportunity.yes_price,
                        "no_ask": opportunity.no_price,
                        "sum": opportunity.price_sum,
                        "shares": shares,
                        "total_cost": total_cost,
                        "total_net_profit": total_net_profit,
                    },
                )
                trades.append(trade)

            await queries.update_opportunity_status(db, opportunity_id, "executed", executed_at=now)
            logger.info(
                f"[PAPER] Binary arb executed: {opportunity.market_question} "
                f"shares={shares:.2f} cost=${total_cost:.2f} net=${total_net_profit:.4f}"
            )
        else:
            try:
                private_key = decrypt_private_key(config.encrypted_private_key)
                order_results = await self._place_binary_orders(
                    private_key=private_key,
                    condition_id=opportunity.condition_id,
                    prices=prices,
                    shares=shares,
                )
                for side, price in prices.items():
                    order_result = order_results.get(side, {})
                    trade = await queries.create_trade(
                        db,
                        user_id=user_id,
                        opportunity_id=opportunity_id,
                        market_id=opportunity.market_id,
                        condition_id=opportunity.condition_id,
                        strategy_type="binary",
                        side=side,
                        size_usdc=Decimal(str(per_leg_cost[side])),
                        fill_price=Decimal(str(price)),
                        fees_paid=Decimal(str(fee_per_leg)),
                        status="submitted",
                        is_paper=False,
                        order_id=order_result.get("order_id"),
                        tx_hash=order_result.get("tx_hash"),
                        price_snapshot={
                            "yes_ask": opportunity.yes_price,
                            "no_ask": opportunity.no_price,
                            "shares": shares,
                        },
                    )
                    trades.append(trade)

                await queries.update_opportunity_status(db, opportunity_id, "executed", executed_at=now)
            except Exception as e:
                logger.error(f"Failed to execute binary arb: {e}")
                await self._cancel_orders(trades)
                await queries.update_opportunity_status(db, opportunity_id, "failed")
                return ExecutionResult(success=False, trades=[], error=str(e))

        return ExecutionResult(
            success=True,
            trades=[{"id": str(t.id), "side": t.side, "status": t.status} for t in trades],
        )

    async def _reverify_binary(
        self, db: AsyncSession, settings: dict, opportunity: BinaryArbitrageResult, opportunity_id: UUID
    ) -> Optional[str]:
        """Re-fetch order books and confirm the opportunity still clears. Returns error string or None."""
        yes_book = await self.polymarket.get_order_book(opportunity.condition_id + "_yes")
        no_book = await self.polymarket.get_order_book(opportunity.condition_id + "_no")

        if not yes_book or not no_book:
            await queries.update_opportunity_status(db, opportunity_id, "failed")
            return "Failed to re-fetch order books"

        new_yes_ask = PolymarketService.get_best_ask_price(yes_book)
        new_no_ask = PolymarketService.get_best_ask_price(no_book)

        if new_yes_ask is None or new_no_ask is None:
            await queries.update_opportunity_status(db, opportunity_id, "failed")
            return "Price no longer available"

        new_sum = new_yes_ask + new_no_ask
        if new_sum >= 1.0:
            await queries.update_opportunity_status(db, opportunity_id, "expired")
            return f"Price moved: new sum {new_sum:.4f} >= 1.0"

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
        if not new_result.is_profitable or new_result.net_profit_pct < min_pct:
            await queries.update_opportunity_status(db, opportunity_id, "expired")
            return f"Opportunity no longer profitable after re-check: {new_result.net_profit_pct:.2f}%"
        return None

    async def execute_multi_outcome_arbitrage(
        self,
        db: AsyncSession,
        user_id: UUID,
        config: UserConfig,
        opportunity: MultiOutcomeArbitrageResult,
        opportunity_id: UUID,
    ) -> ExecutionResult:
        """Execute a multi-outcome arbitrage trade with equal shares across every outcome."""
        settings = config.settings
        is_paper = settings.get("paper_trading", True)
        max_trade = settings.get("max_trade_size", 100.0)

        # ── Atomic claim ──
        claimed = await queries.claim_opportunity(db, opportunity_id, "queued", "executing")
        if not claimed:
            logger.info(f"Opportunity {opportunity_id} already claimed/executing; skipping")
            return ExecutionResult(success=False, trades=[], error="Opportunity already claimed")

        target_notional = min(max_trade, opportunity.min_liquidity)
        prices = dict(opportunity.outcome_prices)
        shares, per_leg_cost = compute_leg_sizes(prices, target_notional)
        total_cost, total_fee, total_net_profit = _arb_pnl(
            shares, opportunity.price_sum, opportunity.fee_worst_case, opportunity.gas_cost
        )

        if shares <= 0:
            await queries.update_opportunity_status(db, opportunity_id, "failed")
            return ExecutionResult(success=False, trades=[], error="Computed zero share size")

        risk_error = await self.check_risk_limits(db, user_id, config, total_cost)
        if risk_error:
            await queries.update_opportunity_status(db, opportunity_id, "failed")
            return ExecutionResult(success=False, trades=[], error=risk_error)

        now = datetime.now(timezone.utc)
        trades = []
        num_legs = len(prices)
        fee_per_leg = total_fee / num_legs
        pnl_per_leg = total_net_profit / num_legs

        if is_paper:
            for outcome, price in prices.items():
                trade = await queries.create_trade(
                    db,
                    user_id=user_id,
                    opportunity_id=opportunity_id,
                    market_id=opportunity.market_id,
                    condition_id=opportunity.condition_id,
                    strategy_type="multi_outcome",
                    side=outcome,
                    size_usdc=Decimal(str(per_leg_cost[outcome])),
                    fill_price=Decimal(str(price)),
                    filled_size=Decimal(str(shares)),  # equal shares on every leg
                    fees_paid=Decimal(str(fee_per_leg)),
                    profit_loss=Decimal(str(pnl_per_leg)),
                    status="filled",
                    is_paper=True,
                    price_snapshot={
                        "outcome_prices": prices,
                        "shares": shares,
                        "total_cost": total_cost,
                        "total_net_profit": total_net_profit,
                    },
                )
                trades.append(trade)

            await queries.update_opportunity_status(db, opportunity_id, "executed", executed_at=now)
            logger.info(
                f"[PAPER] Multi-outcome arb executed: {opportunity.market_question} "
                f"outcomes={num_legs} shares={shares:.2f} cost=${total_cost:.2f} net=${total_net_profit:.4f}"
            )
        else:
            try:
                private_key = decrypt_private_key(config.encrypted_private_key)
                placed = []
                for outcome, price in prices.items():
                    try:
                        order_result = await self._place_outcome_order(
                            private_key=private_key,
                            condition_id=opportunity.condition_id,
                            outcome=outcome,
                            price=price,
                            shares=shares,
                        )
                        trade = await queries.create_trade(
                            db,
                            user_id=user_id,
                            opportunity_id=opportunity_id,
                            market_id=opportunity.market_id,
                            condition_id=opportunity.condition_id,
                            strategy_type="multi_outcome",
                            side=outcome,
                            size_usdc=Decimal(str(per_leg_cost[outcome])),
                            fill_price=Decimal(str(price)),
                            fees_paid=Decimal(str(fee_per_leg)),
                            status="submitted",
                            is_paper=False,
                            order_id=order_result.get("order_id"),
                            tx_hash=order_result.get("tx_hash"),
                            price_snapshot={"outcome_prices": prices, "shares": shares},
                        )
                        trades.append(trade)
                        placed.append(order_result)
                    except Exception as e:
                        logger.error(f"Failed to place order for outcome {outcome}: {e}")
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

    async def execute_strategy_intent(
        self,
        db: AsyncSession,
        user_id: UUID,
        config: UserConfig,
        intent,  # engine.StrategyIntent
        opportunity_id: UUID,
    ) -> ExecutionResult:
        """
        Execute a single-leg strategy intent (buy to open / sell to close).
        Reuses the SAME claim + risk-limit spine as arbitrage — strategy mode is
        not a parallel execution path; it just produces different intents.
        """
        settings = config.settings
        is_paper = settings.get("paper_trading", True)

        claimed = await queries.claim_opportunity(db, opportunity_id, "queued", "executing")
        if not claimed:
            return ExecutionResult(success=False, trades=[], error="Opportunity already claimed")

        is_buy = intent.side == "buy"
        notional = intent.target_qty * intent.limit_price

        # Buys deploy new capital -> full risk gate. Sells only reduce exposure,
        # so they must still honor the kill switch but not the capital/budget caps.
        if is_buy:
            risk_error = await self.check_risk_limits(db, user_id, config, notional)
        else:
            risk_error = None if settings.get("bot_active", False) else "Bot is not active (kill switch may be engaged)"
        if risk_error:
            await queries.update_opportunity_status(db, opportunity_id, "failed")
            return ExecutionResult(success=False, trades=[], error=risk_error)

        strategy_uuid = None
        if getattr(intent, "strategy_id", None):
            try:
                strategy_uuid = UUID(str(intent.strategy_id))
            except (ValueError, TypeError):
                strategy_uuid = None

        # Realized P&L only exists when closing against a known average entry.
        profit_loss = 0.0
        if not is_buy:
            positions = await queries.get_open_positions(db, user_id, strategy_id=strategy_uuid)
            pos = positions.get(intent.token_id)
            if pos:
                close_qty = min(intent.target_qty, pos["qty"])
                profit_loss = (intent.limit_price - pos["avg_entry_price"]) * close_qty

        signed_qty = intent.target_qty if is_buy else -intent.target_qty
        now = datetime.now(timezone.utc)

        if is_paper:
            trade = await queries.create_trade(
                db,
                user_id=user_id,
                opportunity_id=opportunity_id,
                market_id=intent.market_id,
                condition_id=intent.condition_id,
                token_id=intent.token_id,
                strategy_id=strategy_uuid,
                strategy_type="strategy",
                side=f"{intent.side}:{intent.outcome}",
                size_usdc=Decimal(str(notional)),
                fill_price=Decimal(str(intent.limit_price)),
                filled_size=Decimal(str(signed_qty)),
                fees_paid=Decimal("0"),
                profit_loss=Decimal(str(profit_loss)),
                status="filled",
                is_paper=True,
                price_snapshot={"reason": intent.reason, "side": intent.side, "token_id": intent.token_id},
            )
            await queries.update_opportunity_status(db, opportunity_id, "executed", executed_at=now)
            logger.info(
                f"[PAPER] Strategy {intent.reason}: {intent.side} {intent.target_qty:.2f} "
                f"{intent.outcome}@{intent.limit_price:.4f} (${notional:.2f})"
            )
        else:
            try:
                private_key = decrypt_private_key(config.encrypted_private_key)
                order_result = await self._place_outcome_order(
                    private_key=private_key,
                    condition_id=intent.condition_id,
                    outcome=intent.outcome,
                    price=intent.limit_price,
                    shares=intent.target_qty,
                )
                trade = await queries.create_trade(
                    db,
                    user_id=user_id,
                    opportunity_id=opportunity_id,
                    market_id=intent.market_id,
                    condition_id=intent.condition_id,
                    token_id=intent.token_id,
                    strategy_id=strategy_uuid,
                    strategy_type="strategy",
                    side=f"{intent.side}:{intent.outcome}",
                    size_usdc=Decimal(str(notional)),
                    fill_price=Decimal(str(intent.limit_price)),
                    status="submitted",
                    is_paper=False,
                    order_id=order_result.get("order_id"),
                    tx_hash=order_result.get("tx_hash"),
                    price_snapshot={"reason": intent.reason, "side": intent.side, "token_id": intent.token_id},
                )
                await queries.update_opportunity_status(db, opportunity_id, "executed", executed_at=now)
            except Exception as e:
                logger.error(f"Failed to execute strategy intent: {e}")
                await queries.update_opportunity_status(db, opportunity_id, "failed")
                return ExecutionResult(success=False, trades=[], error=str(e))

        return ExecutionResult(
            success=True,
            trades=[{"id": str(trade.id), "side": trade.side, "status": trade.status}],
        )

    async def _place_binary_orders(
        self, private_key: str, condition_id: str, prices: dict[str, float], shares: float,
    ) -> dict[str, dict]:
        """
        Place equal-share YES and NO limit orders via py-clob-client.
        In production this is replaced by the real CLOB client (Phase 3).
        """
        logger.info(f"Placing binary orders: {prices} shares={shares}")
        return {side: {"order_id": f"mock_{side}_order", "tx_hash": None} for side in prices}

    async def _place_outcome_order(
        self, private_key: str, condition_id: str, outcome: str, price: float, shares: float,
    ) -> dict:
        """Place a single outcome limit order for `shares` shares at `price`."""
        logger.info(f"Placing order: {outcome}@{price} shares={shares}")
        return {"order_id": f"mock_{outcome}_order", "tx_hash": None}

    async def _cancel_orders(self, trades: list) -> None:
        """Attempt to cancel all orders from a failed trade set."""
        for trade in trades:
            if hasattr(trade, "order_id") and trade.order_id:
                logger.warning(f"Attempting to cancel order: {trade.order_id}")
                # In production (Phase 3): client.cancel_order(trade.order_id)
