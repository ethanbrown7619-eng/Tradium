"""
Polymarket API integration service.
Handles market data fetching, order book retrieval, and USDC balance checks.
Uses Polymarket's Gamma API for market listings and CLOB API for order books.
"""
import httpx
import logging
from typing import Optional
from app.config import get_settings

logger = logging.getLogger(__name__)


class PolymarketService:
    def __init__(self):
        self.settings = get_settings()
        self.gamma_url = self.settings.polymarket_gamma_url
        self.clob_url = self.settings.polymarket_api_url

    async def get_markets(
        self,
        search: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        active_only: bool = True,
    ) -> list[dict]:
        """Fetch markets from Polymarket Gamma API."""
        params: dict = {
            "limit": limit,
            "offset": offset,
            "order": "volume24hr",
            "ascending": "false",
        }
        if active_only:
            params["active"] = "true"
            params["closed"] = "false"
        if search:
            params["tag"] = search

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{self.gamma_url}/markets", params=params)
                resp.raise_for_status()
                markets = resp.json()

                if category and category != "all":
                    markets = [
                        m for m in markets
                        if category.lower() in [t.lower() for t in m.get("tags", [])]
                    ]

                return markets
        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch markets: {e}")
            return []

    async def get_market(self, market_id: str) -> Optional[dict]:
        """Fetch single market details."""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{self.gamma_url}/markets/{market_id}")
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch market {market_id}: {e}")
            return None

    async def get_order_book(self, token_id: str) -> Optional[dict]:
        """Fetch order book from Polymarket CLOB API for a given token."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.clob_url}/book",
                    params={"token_id": token_id},
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch order book for {token_id}: {e}")
            return None

    async def get_order_books_batch(self, token_ids: list[str]) -> dict[str, dict]:
        """Fetch order books for multiple tokens."""
        results = {}
        async with httpx.AsyncClient(timeout=15) as client:
            for token_id in token_ids:
                try:
                    resp = await client.get(
                        f"{self.clob_url}/book",
                        params={"token_id": token_id},
                    )
                    resp.raise_for_status()
                    results[token_id] = resp.json()
                except httpx.HTTPError as e:
                    logger.warning(f"Failed to fetch order book for {token_id}: {e}")
                    results[token_id] = None
        return results

    async def get_prices(self, token_id: str) -> Optional[dict]:
        """Get current best bid/ask prices for a token."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.clob_url}/price",
                    params={"token_id": token_id, "side": "buy"},
                )
                resp.raise_for_status()
                buy_price = float(resp.json().get("price", 0))

                resp = await client.get(
                    f"{self.clob_url}/price",
                    params={"token_id": token_id, "side": "sell"},
                )
                resp.raise_for_status()
                sell_price = float(resp.json().get("price", 0))

                return {"buy": buy_price, "sell": sell_price}
        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch prices for {token_id}: {e}")
            return None

    async def get_usdc_balance(self, wallet_address: str) -> float:
        """Fetch USDC balance on Polygon for a given wallet address."""
        # USDC on Polygon contract address
        usdc_contract = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    self.settings.polygon_rpc_url,
                    json={
                        "jsonrpc": "2.0",
                        "method": "eth_call",
                        "params": [{
                            "to": usdc_contract,
                            "data": f"0x70a08231000000000000000000000000{wallet_address[2:].lower()}"
                        }, "latest"],
                        "id": 1,
                    }
                )
                resp.raise_for_status()
                result = resp.json().get("result", "0x0")
                # USDC has 6 decimals
                balance = int(result, 16) / 1e6
                return balance
        except Exception as e:
            logger.error(f"Failed to fetch USDC balance: {e}")
            return 0.0

    @staticmethod
    def calculate_order_book_depth(order_book: dict, side: str = "bids") -> float:
        """Calculate total liquidity depth on one side of the order book in USDC."""
        if not order_book:
            return 0.0
        orders = order_book.get(side, [])
        total = 0.0
        for order in orders:
            price = float(order.get("price", 0))
            size = float(order.get("size", 0))
            total += price * size
        return total

    @staticmethod
    def get_best_ask_price(order_book: dict) -> Optional[float]:
        """Get the best (lowest) ask price from order book."""
        if not order_book:
            return None
        asks = order_book.get("asks", [])
        if not asks:
            return None
        # Asks should be sorted ascending; best ask = lowest price
        return min(float(a["price"]) for a in asks)

    @staticmethod
    def get_fillable_price(order_book: dict, side: str, size_usdc: float) -> Optional[float]:
        """
        Calculate the effective fill price for a given size.
        Walks the order book to find what price we'd get for filling `size_usdc`.
        Returns the weighted average fill price, or None if not enough liquidity.
        """
        if not order_book:
            return None

        if side == "buy":
            orders = sorted(order_book.get("asks", []), key=lambda o: float(o["price"]))
        else:
            orders = sorted(order_book.get("bids", []), key=lambda o: -float(o["price"]))

        remaining = size_usdc
        total_tokens = 0.0
        total_cost = 0.0

        for order in orders:
            price = float(order["price"])
            available_size = float(order["size"])
            available_value = price * available_size

            if available_value >= remaining:
                tokens_to_buy = remaining / price
                total_tokens += tokens_to_buy
                total_cost += remaining
                remaining = 0
                break
            else:
                total_tokens += available_size
                total_cost += available_value
                remaining -= available_value

        if remaining > 0:
            return None  # Not enough liquidity

        return total_cost / total_tokens if total_tokens > 0 else None
