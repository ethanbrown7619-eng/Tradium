"""
Real Polymarket CLOB order client (Phase 3).

Wraps py-clob-client for order placement, cancellation, and status polling. This
is the ONLY place that talks to the live order book with a signing key. It is
deliberately behind a small interface so:
  - paper mode never constructs it (no key, no network),
  - the live executor depends on the interface, not py-clob-client directly, and
  - tests inject a FakeOrderClient with the same surface.

py-clob-client is synchronous, so every network call is wrapped in
asyncio.to_thread to avoid blocking the event loop. Order status strings from the
CLOB are normalized to our Trade statuses: filled / partial / submitted /
cancelled / failed.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from app.config import get_settings

logger = logging.getLogger(__name__)

CHAIN_ID_POLYGON = 137


@dataclass
class OrderResult:
    success: bool
    order_id: Optional[str] = None
    status: str = "submitted"          # normalized Trade status
    filled_size: float = 0.0
    fill_price: Optional[float] = None
    tx_hash: Optional[str] = None
    error: Optional[str] = None


def _normalize_status(raw: Optional[str], size: float, matched: float) -> str:
    """Map a CLOB order status + fill amounts to our Trade status vocabulary."""
    s = (raw or "").lower()
    if s in ("matched", "filled", "complete") or (matched > 0 and matched >= size):
        return "filled"
    if s in ("canceled", "cancelled"):
        return "cancelled"
    if matched > 0:
        return "partial"
    if s in ("live", "open", "unmatched", "delayed"):
        return "submitted"
    return "submitted"


class ClobOrderClient:
    """Live CLOB client. Constructs and caches a py-clob-client per private key."""

    def __init__(self, host: Optional[str] = None, chain_id: int = CHAIN_ID_POLYGON):
        settings = get_settings()
        self.host = host or settings.polymarket_api_url
        self.chain_id = chain_id
        self._clients: dict = {}  # private_key -> authenticated ClobClient

    def _get_client(self, private_key: str):
        """Build (and cache) an L2-authenticated ClobClient for a signing key."""
        if private_key in self._clients:
            return self._clients[private_key]
        # Lazy import so the rest of the app (and paper mode) never needs py-clob-client
        from py_clob_client.client import ClobClient

        client = ClobClient(self.host, key=private_key, chain_id=self.chain_id)
        # Derive API credentials from the wallet and attach them (L2 auth)
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)
        self._clients[private_key] = client
        return client

    async def place_order(
        self, private_key: str, token_id: str, side: str, price: float, size: float, tif: str = "GTC",
    ) -> OrderResult:
        """
        Place a limit order. `side` is "buy"/"sell", `size` is in shares, `price`
        is per-share. Always a LIMIT order — never market — to bound slippage.
        """
        try:
            return await asyncio.to_thread(self._place_order_sync, private_key, token_id, side, price, size, tif)
        except Exception as e:  # network/signing/validation failure
            logger.error(f"CLOB place_order failed for token {token_id}: {e}")
            return OrderResult(success=False, status="failed", error=str(e))

    def _place_order_sync(self, private_key, token_id, side, price, size, tif) -> OrderResult:
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY, SELL

        client = self._get_client(private_key)
        order_args = OrderArgs(
            price=round(float(price), 3),
            size=float(size),
            side=BUY if side == "buy" else SELL,
            token_id=str(token_id),
        )
        signed = client.create_order(order_args)
        order_type = getattr(OrderType, tif, OrderType.GTC)
        resp = client.post_order(signed, order_type)

        order_id = resp.get("orderID") or resp.get("order_id")
        matched = float(resp.get("sizeMatched", 0) or 0)
        status = _normalize_status(resp.get("status"), float(size), matched)
        success = bool(resp.get("success", order_id is not None))
        return OrderResult(
            success=success,
            order_id=order_id,
            status=status if success else "failed",
            filled_size=matched,
            fill_price=float(price),
            error=None if success else str(resp.get("errorMsg") or resp),
        )

    async def cancel_order(self, private_key: str, order_id: str) -> bool:
        try:
            return await asyncio.to_thread(self._cancel_order_sync, private_key, order_id)
        except Exception as e:
            logger.error(f"CLOB cancel_order failed for {order_id}: {e}")
            return False

    def _cancel_order_sync(self, private_key, order_id) -> bool:
        client = self._get_client(private_key)
        resp = client.cancel(order_id=order_id)
        # cancel returns {"canceled": [...], "not_canceled": {...}}
        canceled = resp.get("canceled", []) if isinstance(resp, dict) else []
        return order_id in canceled or bool(canceled)

    async def cancel_all(self, private_key: str) -> bool:
        try:
            return await asyncio.to_thread(self._cancel_all_sync, private_key)
        except Exception as e:
            logger.error(f"CLOB cancel_all failed: {e}")
            return False

    def _cancel_all_sync(self, private_key) -> bool:
        client = self._get_client(private_key)
        client.cancel_all()
        return True

    async def get_order(self, private_key: str, order_id: str) -> OrderResult:
        try:
            return await asyncio.to_thread(self._get_order_sync, private_key, order_id)
        except Exception as e:
            logger.error(f"CLOB get_order failed for {order_id}: {e}")
            return OrderResult(success=False, order_id=order_id, status="submitted", error=str(e))

    def _get_order_sync(self, private_key, order_id) -> OrderResult:
        client = self._get_client(private_key)
        o = client.get_order(order_id)
        size = float(o.get("original_size", o.get("size", 0)) or 0)
        matched = float(o.get("size_matched", 0) or 0)
        price = o.get("price")
        status = _normalize_status(o.get("status"), size, matched)
        return OrderResult(
            success=True,
            order_id=order_id,
            status=status,
            filled_size=matched,
            fill_price=float(price) if price is not None else None,
        )
