"""
High-level arbitrage coordinator.
Ties together market data fetching, strategy detection, and opportunity queuing.
"""
import logging
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import queries
from app.services.polymarket import PolymarketService
from app.bot.strategies.binary import scan_binary_markets, BinaryArbitrageResult
from app.bot.strategies.multioutcome import scan_multi_outcome_markets, MultiOutcomeArbitrageResult
from app.bot.strategies.correlated import detect_correlated_opportunities, CorrelatedOpportunity

logger = logging.getLogger(__name__)


class ArbitrageDetector:
    def __init__(self, polymarket: PolymarketService):
        self.polymarket = polymarket

    async def scan_all(
        self,
        db: AsyncSession,
        user_id: UUID,
        settings: dict,
    ) -> dict:
        """
        Full scan cycle: fetch markets, detect all arbitrage types, queue opportunities.
        Returns summary of findings.
        """
        min_liquidity = settings.get("min_liquidity", 500.0)
        min_profit_pct = settings.get("min_profit_pct", 1.0)
        min_profit_usdc = settings.get("min_profit_usdc", 0.50)
        categories = settings.get("categories", ["all"])
        excluded_markets = settings.get("excluded_markets", [])
        excluded_keywords = settings.get("excluded_keywords", [])

        # Fetch active markets
        markets = await self.polymarket.get_markets(limit=200, active_only=True)
        if not markets:
            logger.warning("No markets fetched from Polymarket")
            return {"binary": [], "multi_outcome": [], "correlated": [], "markets_scanned": 0}

        # Apply filters
        filtered = []
        for m in markets:
            mid = m.get("id", "")
            question = m.get("question", "")

            if mid in excluded_markets:
                continue
            if any(kw.lower() in question.lower() for kw in excluded_keywords):
                continue
            if "all" not in categories:
                tags = [t.lower() for t in m.get("tags", [])]
                if not any(c.lower() in tags for c in categories):
                    continue
            filtered.append(m)

        # Cache markets
        for m in filtered:
            await queries.upsert_market_cache(
                db, m.get("id", ""), m.get("condition_id", ""), m
            )

        # Collect all token IDs for order book fetching
        all_token_ids = []
        for m in filtered:
            for token in m.get("tokens", []):
                tid = token.get("token_id", "")
                if tid:
                    all_token_ids.append(tid)

        # Fetch order books
        order_books = await self.polymarket.get_order_books_batch(all_token_ids)

        # ── Strategy 1: Binary arbitrage ──
        binary_results = scan_binary_markets(filtered, order_books, min_liquidity)
        binary_opportunities = []
        for r in binary_results:
            if r.net_profit_pct >= min_profit_pct:
                trade_size = min(settings.get("max_trade_size", 100.0), r.min_liquidity)
                profit_usdc = r.net_profit * trade_size
                if profit_usdc >= min_profit_usdc:
                    opp = await queries.create_opportunity(
                        db,
                        user_id=user_id,
                        market_id=r.market_id,
                        condition_id=r.condition_id,
                        market_slug=r.market_slug,
                        market_question=r.market_question,
                        strategy_type="binary",
                        outcome_prices={"YES": r.yes_price, "NO": r.no_price},
                        price_sum=r.price_sum,
                        estimated_profit_pct=r.net_profit_pct,
                        estimated_profit_usdc=profit_usdc,
                        liquidity_depth=r.min_liquidity,
                        status="queued" if settings.get("auto_execute_binary") else "pending",
                    )
                    binary_opportunities.append(opp)

        # ── Strategy 2: Multi-outcome arbitrage ──
        multi_results = scan_multi_outcome_markets(filtered, order_books, min_liquidity)
        multi_opportunities = []
        for r in multi_results:
            if r.net_profit_pct >= min_profit_pct:
                trade_size = min(settings.get("max_trade_size", 100.0), r.min_liquidity)
                profit_usdc = r.net_profit * trade_size
                if profit_usdc >= min_profit_usdc:
                    opp = await queries.create_opportunity(
                        db,
                        user_id=user_id,
                        market_id=r.market_id,
                        condition_id=r.condition_id,
                        market_slug=r.market_slug,
                        market_question=r.market_question,
                        strategy_type="multi_outcome",
                        outcome_prices=r.outcome_prices,
                        price_sum=r.price_sum,
                        estimated_profit_pct=r.net_profit_pct,
                        estimated_profit_usdc=profit_usdc,
                        liquidity_depth=r.min_liquidity,
                        status="queued" if settings.get("auto_execute_multi") else "pending",
                    )
                    multi_opportunities.append(opp)

        # ── Strategy 3: Correlated market detection ──
        correlated = detect_correlated_opportunities(filtered)
        correlated_opportunities = []
        for c in correlated:
            opp = await queries.create_opportunity(
                db,
                user_id=user_id,
                market_id=c.market_a_id,
                market_question=f"{c.market_a_question} vs {c.market_b_question}",
                strategy_type="correlated",
                outcome_prices={
                    "market_a": c.market_a_price,
                    "market_b": c.market_b_price,
                },
                price_sum=c.market_a_price + c.market_b_price,
                estimated_profit_pct=c.estimated_profit_pct,
                confidence_score=c.confidence_score,
                status="flagged",  # Never auto-execute correlated
            )
            correlated_opportunities.append(opp)

        return {
            "markets_scanned": len(filtered),
            "binary": binary_opportunities,
            "multi_outcome": multi_opportunities,
            "correlated": correlated_opportunities,
        }
