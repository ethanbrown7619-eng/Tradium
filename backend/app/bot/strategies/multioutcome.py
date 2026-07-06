"""
Multi-Outcome Market Arbitrage Strategy (Strategy 2)

Markets with 3+ mutually exclusive outcomes that must sum to $1.00 at settlement.
If the sum of all best ask prices < $1.00 minus fees, buy all outcomes for guaranteed profit.

Fee: 2% on winnings. If we buy all N outcomes, the winning one pays $1.00.
Winnings = 1.00 - cost_of_winning_outcome.
Worst case fee = 0.02 * (1.00 - min_outcome_price), since cheapest = highest winnings.
"""
from dataclasses import dataclass
from typing import Optional
import logging

logger = logging.getLogger(__name__)

FEE_RATE = 0.02
DEFAULT_GAS_COST_PER_TX = 0.005


@dataclass
class MultiOutcomeArbitrageResult:
    market_id: str
    condition_id: str
    market_question: str
    market_slug: str
    outcome_prices: dict[str, float]  # outcome_name -> ask price
    outcome_liquidities: dict[str, float]
    price_sum: float
    gross_profit: float
    fee_worst_case: float
    gas_cost: float
    net_profit: float
    net_profit_pct: float
    min_liquidity: float
    num_outcomes: int
    is_profitable: bool


def calculate_multi_outcome_arbitrage(
    market_id: str,
    condition_id: str,
    market_question: str,
    market_slug: str,
    outcome_prices: dict[str, float],
    outcome_liquidities: dict[str, float] = None,
    gas_cost_per_tx: float = DEFAULT_GAS_COST_PER_TX,
) -> MultiOutcomeArbitrageResult:
    """
    Calculate multi-outcome arbitrage.
    All outcome_prices should be best ask prices.
    """
    if outcome_liquidities is None:
        outcome_liquidities = {k: 0.0 for k in outcome_prices}

    num_outcomes = len(outcome_prices)
    price_sum = sum(outcome_prices.values())
    gross_profit = 1.0 - price_sum

    # Worst case fee: cheapest outcome wins -> highest winnings -> highest fee
    min_price = min(outcome_prices.values())
    fee_worst_case = FEE_RATE * max(0, 1.0 - min_price)

    gas_cost = gas_cost_per_tx * num_outcomes

    net_profit = gross_profit - fee_worst_case - gas_cost
    net_profit_pct = (net_profit / price_sum * 100) if price_sum > 0 else 0.0
    min_liquidity = min(outcome_liquidities.values()) if outcome_liquidities else 0.0

    return MultiOutcomeArbitrageResult(
        market_id=market_id,
        condition_id=condition_id,
        market_question=market_question,
        market_slug=market_slug,
        outcome_prices=outcome_prices,
        outcome_liquidities=outcome_liquidities,
        price_sum=price_sum,
        gross_profit=gross_profit,
        fee_worst_case=fee_worst_case,
        gas_cost=gas_cost,
        net_profit=net_profit,
        net_profit_pct=net_profit_pct,
        min_liquidity=min_liquidity,
        num_outcomes=num_outcomes,
        is_profitable=net_profit > 0,
    )


def scan_multi_outcome_markets(
    markets: list[dict],
    order_books: dict,
    min_liquidity: float = 500.0,
) -> list[MultiOutcomeArbitrageResult]:
    """Scan markets with 3+ outcomes for arbitrage."""
    results = []

    for market in markets:
        tokens = market.get("tokens", [])
        if len(tokens) < 3:
            continue

        outcome_prices = {}
        outcome_liquidities = {}
        all_valid = True

        for token in tokens:
            outcome = token.get("outcome", "")
            token_id = token.get("token_id", "")
            book = order_books.get(token_id)

            if not book:
                all_valid = False
                break

            # VWAP price for filling the intended size on this outcome. If any
            # outcome can't fill `min_liquidity`, the whole basket isn't tradeable
            # (multi-outcome arb requires filling ALL legs simultaneously).
            from app.services.polymarket import PolymarketService
            ask = PolymarketService.get_fillable_price(book, "buy", min_liquidity)
            if ask is None:
                all_valid = False
                break

            outcome_prices[outcome] = ask
            outcome_liquidities[outcome] = min_liquidity

        if not all_valid:
            continue

        result = calculate_multi_outcome_arbitrage(
            market_id=market.get("id", ""),
            condition_id=market.get("condition_id", ""),
            market_question=market.get("question", ""),
            market_slug=market.get("slug", ""),
            outcome_prices=outcome_prices,
            outcome_liquidities=outcome_liquidities,
        )

        if result.is_profitable:
            logger.info(
                f"Multi-outcome arb found: {result.market_question} "
                f"Prices={result.outcome_prices} Sum={result.price_sum:.4f} "
                f"Net={result.net_profit_pct:.2f}%"
            )
            results.append(result)

    return results
