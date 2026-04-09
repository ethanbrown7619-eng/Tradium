"""
Binary Market Arbitrage Strategy (Strategy 1)

Every binary market has a YES token and a NO token.
At settlement, exactly one pays $1.00 and the other pays $0.00.
If YES ask + NO ask < $1.00, buying both guarantees profit.

CRITICAL: Polymarket charges a 2% fee on WINNINGS, not on the trade amount.
Winnings = payout - cost_of_winning_token.
Since we buy BOTH sides, the winning side pays $1.00.
Fee = 0.02 * (1.00 - cost_of_winning_side).
We don't know which side wins, but since we hold both:
  - If YES wins: profit = 1.00 - yes_cost - no_cost - 0.02*(1.00 - yes_cost) - gas
  - If NO wins:  profit = 1.00 - yes_cost - no_cost - 0.02*(1.00 - no_cost) - gas
In the worst case, the cheaper token wins (higher winnings = higher fee).
We must use the WORST CASE for safety.
"""
from dataclasses import dataclass
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# Polymarket fee rate on winnings
FEE_RATE = 0.02
# Estimated gas cost in USDC for a Polygon transaction
DEFAULT_GAS_COST = 0.005  # $0.005 per tx, two txs for binary


@dataclass
class BinaryArbitrageResult:
    market_id: str
    condition_id: str
    market_question: str
    market_slug: str
    yes_price: float
    no_price: float
    price_sum: float
    gross_profit: float
    fee_worst_case: float
    gas_cost: float
    net_profit: float
    net_profit_pct: float
    yes_liquidity: float
    no_liquidity: float
    min_liquidity: float
    is_profitable: bool


def calculate_binary_arbitrage(
    market_id: str,
    condition_id: str,
    market_question: str,
    market_slug: str,
    yes_ask: float,
    no_ask: float,
    yes_liquidity: float = 0.0,
    no_liquidity: float = 0.0,
    gas_cost: float = DEFAULT_GAS_COST * 2,
) -> BinaryArbitrageResult:
    """
    Calculate whether a binary arbitrage opportunity exists.

    Args:
        yes_ask: Best ask price for YES token (cost to buy YES)
        no_ask: Best ask price for NO token (cost to buy NO)
        gas_cost: Total estimated gas for both transactions
    """
    price_sum = yes_ask + no_ask
    gross_profit = 1.0 - price_sum

    # Fee calculation: 2% on winnings for the winning side.
    # Winnings on YES side = 1.00 - yes_ask
    # Winnings on NO side = 1.00 - no_ask
    # Worst case = higher winnings = higher fee = min(yes_ask, no_ask) side wins
    fee_if_yes_wins = FEE_RATE * max(0, 1.0 - yes_ask)
    fee_if_no_wins = FEE_RATE * max(0, 1.0 - no_ask)
    fee_worst_case = max(fee_if_yes_wins, fee_if_no_wins)

    net_profit = gross_profit - fee_worst_case - gas_cost
    net_profit_pct = (net_profit / price_sum * 100) if price_sum > 0 else 0.0

    min_liquidity = min(yes_liquidity, no_liquidity)

    return BinaryArbitrageResult(
        market_id=market_id,
        condition_id=condition_id,
        market_question=market_question,
        market_slug=market_slug,
        yes_price=yes_ask,
        no_price=no_ask,
        price_sum=price_sum,
        gross_profit=gross_profit,
        fee_worst_case=fee_worst_case,
        gas_cost=gas_cost,
        net_profit=net_profit,
        net_profit_pct=net_profit_pct,
        yes_liquidity=yes_liquidity,
        no_liquidity=no_liquidity,
        min_liquidity=min_liquidity,
        is_profitable=net_profit > 0,
    )


def scan_binary_markets(markets: list[dict], order_books: dict, min_liquidity: float = 500.0) -> list[BinaryArbitrageResult]:
    """
    Scan a list of binary markets for arbitrage opportunities.

    Args:
        markets: List of market dicts from Polymarket API
        order_books: Dict mapping token_id -> order_book data
        min_liquidity: Minimum liquidity depth in USDC on each side
    """
    results = []

    for market in markets:
        # Binary markets have exactly 2 tokens
        tokens = market.get("tokens", [])
        if len(tokens) != 2:
            continue

        # Identify YES and NO tokens
        yes_token = None
        no_token = None
        for token in tokens:
            outcome = token.get("outcome", "").upper()
            if outcome == "YES":
                yes_token = token
            elif outcome == "NO":
                no_token = token

        if not yes_token or not no_token:
            continue

        yes_token_id = yes_token.get("token_id", "")
        no_token_id = no_token.get("token_id", "")

        yes_book = order_books.get(yes_token_id)
        no_book = order_books.get(no_token_id)

        if not yes_book or not no_book:
            continue

        # Get best ask prices
        from app.services.polymarket import PolymarketService
        yes_ask = PolymarketService.get_best_ask_price(yes_book)
        no_ask = PolymarketService.get_best_ask_price(no_book)

        if yes_ask is None or no_ask is None:
            continue

        # Calculate liquidity depth
        yes_liq = PolymarketService.calculate_order_book_depth(yes_book, "asks")
        no_liq = PolymarketService.calculate_order_book_depth(no_book, "asks")

        if min(yes_liq, no_liq) < min_liquidity:
            continue

        result = calculate_binary_arbitrage(
            market_id=market.get("id", ""),
            condition_id=market.get("condition_id", ""),
            market_question=market.get("question", ""),
            market_slug=market.get("slug", ""),
            yes_ask=yes_ask,
            no_ask=no_ask,
            yes_liquidity=yes_liq,
            no_liquidity=no_liq,
        )

        if result.is_profitable:
            logger.info(
                f"Binary arb found: {result.market_question} "
                f"YES={result.yes_price:.4f} NO={result.no_price:.4f} "
                f"Net={result.net_profit_pct:.2f}%"
            )
            results.append(result)

    return results
