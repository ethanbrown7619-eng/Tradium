"""
Polymarket API integration service.
Handles market data fetching, order book retrieval, and USDC balance checks.
Uses Polymarket's Gamma API for market listings and CLOB API for order books.
"""
import httpx
import json
import logging
from typing import Optional
from app.config import get_settings

logger = logging.getLogger(__name__)


def _parse_json_list(value) -> list:
    """
    Parse a Gamma field that may be a JSON-encoded string (e.g. clobTokenIds =
    '["123","456"]') or already a list. Returns [] on anything unparseable.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, ValueError):
            return []
    return []


class PolymarketService:
    def __init__(self):
        self.settings = get_settings()
        self.gamma_url = self.settings.polymarket_gamma_url
        self.clob_url = self.settings.polymarket_api_url
        # Keep CLOB API usage under limits (shared across this service instance)
        from app.services.ratelimit import AsyncRateLimiter
        self._rate_limiter = AsyncRateLimiter(max_calls=8, period=1.0)

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

    async def get_clob_markets(self, max_markets: int = 500) -> list[dict]:
        """
        Fetch tradable markets from the CLOB /markets endpoint (cursor-paginated).

        The CLOB endpoint is the source of truth for tradable token IDs: each market
        carries a `tokens[]` array with real `token_id`/`outcome`. (The Gamma API, by
        contrast, returns `clobTokenIds` as a JSON-encoded string and camelCase keys —
        which is why the old scanner, reading `market["tokens"]` off Gamma, saw nothing.)
        """
        markets: list[dict] = []
        cursor = ""
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                while len(markets) < max_markets:
                    params = {"next_cursor": cursor} if cursor else {}
                    resp = await client.get(f"{self.clob_url}/markets", params=params)
                    resp.raise_for_status()
                    body = resp.json()
                    page = body.get("data", []) if isinstance(body, dict) else body
                    if not page:
                        break
                    markets.extend(page)
                    next_cursor = body.get("next_cursor", "") if isinstance(body, dict) else ""
                    # Stop on ANY end condition: the documented sentinel "LTE=" (base64
                    # for -1), a missing/empty cursor, OR a cursor that didn't advance
                    # (loop guard — terminates safely even if the API misbehaves).
                    if not next_cursor or next_cursor == "LTE=" or next_cursor == cursor:
                        break
                    cursor = next_cursor
        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch CLOB markets: {e}")
        return markets[:max_markets]

    async def get_active_markets(self, max_markets: int = 500) -> list[dict]:
        """Fetch and normalize active, tradable markets into a stable internal shape."""
        raw = await self.get_clob_markets(max_markets=max_markets)
        normalized = []
        for m in raw:
            norm = self.normalize_market(m)
            if norm and norm["active"] and not norm["closed"] and norm["tokens"]:
                normalized.append(norm)
        return normalized

    @staticmethod
    def normalize_market(raw: dict) -> Optional[dict]:
        """
        Normalize a market from EITHER the CLOB /markets shape or the Gamma shape
        into one stable dict the scanner/strategies consume:

            {id, market_id, condition_id, slug, question, tokens:[{token_id, outcome}],
             tags, category, active, closed, min_tick_size, min_order_size, volume, raw}

        Returns None if the raw record has no usable token IDs.
        """
        if not isinstance(raw, dict):
            return None

        tokens: list[dict] = []

        # ── CLOB native shape: tokens[] already has token_id/outcome ──
        if isinstance(raw.get("tokens"), list) and raw["tokens"] and isinstance(raw["tokens"][0], dict) \
                and raw["tokens"][0].get("token_id"):
            for t in raw["tokens"]:
                tid = t.get("token_id")
                if tid:
                    tokens.append({"token_id": str(tid), "outcome": t.get("outcome", "")})
            condition_id = raw.get("condition_id") or raw.get("conditionId") or ""
            market_id = condition_id or raw.get("question_id") or ""
            slug = raw.get("market_slug") or raw.get("slug") or ""
            question = raw.get("question") or ""
            tags = raw.get("tags") or []
            category = raw.get("category") or ""
            active = bool(raw.get("active", True))
            closed = bool(raw.get("closed", False))
            min_tick = raw.get("minimum_tick_size") or raw.get("min_tick_size")
            min_size = raw.get("minimum_order_size") or raw.get("min_order_size")
            volume = float(raw.get("volume") or 0) if raw.get("volume") not in (None, "") else 0.0

        # ── Gamma shape: clobTokenIds + outcomes are JSON-encoded strings ──
        elif raw.get("clobTokenIds"):
            token_ids = _parse_json_list(raw.get("clobTokenIds"))
            outcomes = _parse_json_list(raw.get("outcomes"))
            for i, tid in enumerate(token_ids):
                if tid:
                    outcome = outcomes[i] if i < len(outcomes) else ""
                    tokens.append({"token_id": str(tid), "outcome": outcome})
            condition_id = raw.get("conditionId") or raw.get("condition_id") or ""
            market_id = str(raw.get("id") or condition_id or "")
            slug = raw.get("slug") or ""
            question = raw.get("question") or ""
            tags = raw.get("tags") or []
            category = raw.get("category") or ""
            active = bool(raw.get("active", True))
            closed = bool(raw.get("closed", False))
            min_tick = raw.get("orderPriceMinTickSize")
            min_size = raw.get("orderMinSize")
            volume = float(raw.get("volumeNum") or raw.get("volume") or 0) if raw.get("volumeNum") or raw.get("volume") else 0.0
        else:
            return None

        if not tokens:
            return None

        return {
            "id": market_id,
            "market_id": market_id,
            "condition_id": condition_id,
            "slug": slug,
            "question": question,
            "tokens": tokens,
            "tags": tags,
            "category": category,
            "active": active,
            "closed": closed,
            "min_tick_size": float(min_tick) if min_tick not in (None, "") else None,
            "min_order_size": float(min_size) if min_size not in (None, "") else None,
            "volume": volume,
            "raw": raw,
        }

    async def get_market_resolution(self, condition_id: str) -> dict:
        """
        Check whether a market has resolved and which token won.
        Returns {"resolved": bool, "winning_token_id": str|None}.
        A CLOB market is resolved when it is closed and one token has winner=True.
        """
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.clob_url}/markets/{condition_id}")
                resp.raise_for_status()
                m = resp.json()
        except httpx.HTTPError as e:
            logger.warning(f"Failed to fetch resolution for {condition_id}: {e}")
            return {"resolved": False, "winning_token_id": None}

        closed = bool(m.get("closed", False))
        winning_token_id = None
        for t in m.get("tokens", []):
            if t.get("winner"):
                winning_token_id = str(t.get("token_id"))
                break
        return {"resolved": closed and winning_token_id is not None, "winning_token_id": winning_token_id}

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
        """Fetch order books for multiple tokens, rate-limited to stay under CLOB limits."""
        results = {}
        async with httpx.AsyncClient(timeout=15) as client:
            for token_id in token_ids:
                try:
                    await self._rate_limiter.acquire()
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
    def get_best_bid_price(order_book: dict) -> Optional[float]:
        """Get the best (highest) bid price from order book."""
        if not order_book:
            return None
        bids = order_book.get("bids", [])
        if not bids:
            return None
        # Best bid = highest price someone will pay
        return max(float(b["price"]) for b in bids)

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
