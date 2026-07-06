"""
Background scanner that continuously monitors markets for arbitrage.
Runs as a Celery task, triggered by Celery Beat at the user's configured interval.
"""
import logging
import asyncio
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import async_session
from app.database.schema import User, UserConfig, Opportunity
from app.database import queries
from app.services.polymarket import PolymarketService
from app.bot.arbitrage import ArbitrageDetector
from app.bot.executor import ExecutionEngine
from app.bot.strategies import engine as strategy_engine

logger = logging.getLogger(__name__)

polymarket = PolymarketService()
detector = ArbitrageDetector(polymarket)
executor = ExecutionEngine(polymarket)


async def run_scan_cycle():
    """
    Main scan cycle. Called by Celery task.
    Iterates over all active users and scans for each.
    """
    async with async_session() as db:
        # Get all users with active bots
        result = await db.execute(
            select(UserConfig).where(
                UserConfig.settings["bot_active"].as_boolean() == True
            )
        )
        active_configs = list(result.scalars().all())

        if not active_configs:
            logger.debug("No active bot configurations found")
            return

        for config in active_configs:
            try:
                await _scan_for_user(db, config)
            except Exception as e:
                logger.error(f"Scan failed for user {config.user_id}: {e}", exc_info=True)


async def _scan_for_user(db: AsyncSession, config: UserConfig):
    """Run a single scan cycle for one user."""
    user_id = config.user_id
    settings = config.settings

    logger.info(f"Scanning for user {user_id}")

    # Detect opportunities
    scan_result = await detector.scan_all(db, user_id, settings)

    markets_scanned = scan_result["markets_scanned"]
    binary_count = len(scan_result["binary"])
    multi_count = len(scan_result["multi_outcome"])
    correlated_count = len(scan_result["correlated"])

    logger.info(
        f"Scan complete for user {user_id}: "
        f"{markets_scanned} markets, "
        f"{binary_count} binary, {multi_count} multi, {correlated_count} correlated"
    )

    # Execute queued opportunities
    queued_opps = await queries.get_opportunities(db, user_id, status="queued", limit=10)
    for opp in queued_opps:
        try:
            if opp.strategy_type == "binary":
                result = await executor.execute_binary_arbitrage(
                    db, user_id, config, _opp_to_binary_result(opp), opp.id,
                )
            elif opp.strategy_type == "multi_outcome":
                result = await executor.execute_multi_outcome_arbitrage(
                    db, user_id, config, _opp_to_multi_result(opp), opp.id,
                )
            else:
                continue

            if result.success:
                logger.info(f"Successfully executed opportunity {opp.id}")
            else:
                logger.warning(f"Failed to execute opportunity {opp.id}: {result.error}")

        except Exception as e:
            logger.error(f"Error executing opportunity {opp.id}: {e}", exc_info=True)
            await queries.update_opportunity_status(db, opp.id, "failed")

    # Strategy mode: evaluate user-defined strategies and act on their intents
    try:
        strat_result = await run_strategies_for_user(db, config)
        if strat_result["intents"]:
            logger.info(
                f"Strategies for user {user_id}: {strat_result['intents']} intents, "
                f"{strat_result['executed']} executed"
            )
    except Exception as e:
        logger.error(f"Strategy run failed for user {user_id}: {e}", exc_info=True)

    # Send notifications if configured
    await _send_notifications(db, config, scan_result)


async def run_strategies_for_user(db: AsyncSession, config: UserConfig) -> dict:
    """
    Evaluate all enabled strategies for a user and execute their intents.

    Strategy intents reuse the arbitrage execution spine: each intent is persisted
    as a 'strategy' Opportunity (status 'queued') and then run through
    executor.execute_strategy_intent, which claims and risk-checks it exactly like
    an arbitrage trade. There is no separate execution path.
    """
    user_id = config.user_id
    settings = config.settings

    strategies = await queries.get_strategies(db, user_id, enabled_only=True)
    if not strategies:
        return {"intents": 0, "executed": 0}

    markets = await polymarket.get_active_markets(max_markets=500)
    markets_by_id = {m["market_id"]: m for m in markets}

    # Resolve each strategy's market universe and collect tokens to price
    scoped: dict = {}
    all_token_ids: set = set()
    for strat in strategies:
        uni = (strat.definition or {}).get("universe", {}) or {}
        mids = uni.get("market_ids") or []
        slug = uni.get("slug_contains")
        if mids:
            ms = [markets_by_id[mid] for mid in mids if mid in markets_by_id]
        elif slug:
            ms = [m for m in markets if slug.lower() in (m.get("slug", "").lower())]
        else:
            ms = markets  # "all"
        scoped[str(strat.id)] = ms
        for m in ms:
            for t in m.get("tokens", []):
                all_token_ids.add(t["token_id"])

    order_books = await polymarket.get_order_books_batch(list(all_token_ids))
    budget = settings.get("usdc_budget", 1000.0)

    intents_created = 0
    executed = 0
    for strat in strategies:
        strat_dict = dict(strat.definition or {})
        strat_dict["id"] = str(strat.id)

        positions_raw = await queries.get_open_positions(db, user_id, strategy_id=strat.id)
        positions = {
            tid: strategy_engine.Position(tid, p["qty"], p["avg_entry_price"])
            for tid, p in positions_raw.items()
        }
        open_count = len(positions)

        for m in scoped[str(strat.id)]:
            token_map = strategy_engine.build_token_map(m)
            ctx = strategy_engine.StrategyContext(
                market=m, order_books=order_books, token_map=token_map, positions=positions,
            )
            intent = strategy_engine.evaluate_strategy_for_market(
                strat_dict, m, ctx, budget, open_count,
            )
            if not intent:
                continue

            opp = await queries.create_opportunity(
                db,
                user_id=user_id,
                market_id=intent.market_id,
                condition_id=intent.condition_id,
                market_question=m.get("question", ""),
                strategy_type="strategy",
                outcome_prices={
                    "token_id": intent.token_id,
                    "outcome": intent.outcome,
                    "side": intent.side,
                    "target_qty": intent.target_qty,
                    "limit_price": intent.limit_price,
                    "reason": intent.reason,
                },
                price_sum=intent.limit_price,
                estimated_profit_pct=0,
                status="queued",
            )
            intents_created += 1
            result = await executor.execute_strategy_intent(db, user_id, config, intent, opp.id)
            if result.success:
                executed += 1
                if intent.reason == "entry":
                    open_count += 1

    return {"intents": intents_created, "executed": executed}


def _opp_to_binary_result(opp: Opportunity):
    """Convert an Opportunity DB record to a BinaryArbitrageResult."""
    from app.bot.strategies.binary import BinaryArbitrageResult, FEE_RATE
    prices = opp.outcome_prices
    yes_p = prices.get("YES", 0)
    no_p = prices.get("NO", 0)
    price_sum = yes_p + no_p
    gross = 1.0 - price_sum
    fee = FEE_RATE * max(0, 1.0 - min(yes_p, no_p))
    gas = 0.01
    net = gross - fee - gas

    return BinaryArbitrageResult(
        market_id=opp.market_id,
        condition_id=opp.condition_id or "",
        market_question=opp.market_question or "",
        market_slug=opp.market_slug or "",
        yes_price=yes_p,
        no_price=no_p,
        price_sum=price_sum,
        gross_profit=gross,
        fee_worst_case=fee,
        gas_cost=gas,
        net_profit=net,
        net_profit_pct=float(opp.estimated_profit_pct),
        yes_liquidity=float(opp.liquidity_depth or 0),
        no_liquidity=float(opp.liquidity_depth or 0),
        min_liquidity=float(opp.liquidity_depth or 0),
        is_profitable=net > 0,
    )


def _opp_to_multi_result(opp: Opportunity):
    """Convert an Opportunity DB record to a MultiOutcomeArbitrageResult."""
    from app.bot.strategies.multioutcome import MultiOutcomeArbitrageResult, FEE_RATE
    prices = opp.outcome_prices
    price_sum = sum(prices.values())
    gross = 1.0 - price_sum
    min_p = min(prices.values())
    fee = FEE_RATE * max(0, 1.0 - min_p)
    gas = 0.005 * len(prices)
    net = gross - fee - gas

    return MultiOutcomeArbitrageResult(
        market_id=opp.market_id,
        condition_id=opp.condition_id or "",
        market_question=opp.market_question or "",
        market_slug=opp.market_slug or "",
        outcome_prices=prices,
        outcome_liquidities={k: float(opp.liquidity_depth or 0) for k in prices},
        price_sum=price_sum,
        gross_profit=gross,
        fee_worst_case=fee,
        gas_cost=gas,
        net_profit=net,
        net_profit_pct=float(opp.estimated_profit_pct),
        min_liquidity=float(opp.liquidity_depth or 0),
        num_outcomes=len(prices),
        is_profitable=net > 0,
    )


async def _send_notifications(db: AsyncSession, config: UserConfig, scan_result: dict):
    """Send notifications for scan results if configured."""
    settings = config.settings
    email = settings.get("notify_email")
    if not email:
        return

    from app.services.notifications import NotificationService
    notifier = NotificationService()

    if settings.get("notify_on_opportunity", False):
        for opp_list in [scan_result["binary"], scan_result["multi_outcome"]]:
            for opp in opp_list:
                await notifier.notify_opportunity_found(email, {
                    "market_id": opp.market_id,
                    "market_question": opp.market_question,
                    "strategy_type": opp.strategy_type,
                    "outcome_prices": opp.outcome_prices,
                    "price_sum": float(opp.price_sum),
                    "estimated_profit_pct": float(opp.estimated_profit_pct),
                    "estimated_profit_usdc": float(opp.estimated_profit_usdc or 0),
                    "liquidity_depth": float(opp.liquidity_depth or 0),
                })
