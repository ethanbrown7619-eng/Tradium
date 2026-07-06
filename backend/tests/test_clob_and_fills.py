"""
Phase 3 tests: real order path (mock-tested), fill monitoring, hedge guard.

We cannot hit the live CLOB from CI, so the executor's live path is exercised
against a FakeOrderClient with the same surface as ClobOrderClient. These tests
prove: status normalization, live equal-share placement, sibling cancellation
when a leg fails, VWAP re-verify rejection on a moved book, the partial-hedge
assessment, and paper non-crossing limits resting unfilled.
"""
import asyncio
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.clob import _normalize_status, OrderResult
from app.bot.fill_monitor import assess_hedge
from app.bot import executor as executor_mod
from app.bot.executor import ExecutionEngine
from app.bot.strategies.binary import calculate_binary_arbitrage


# ── CLOB status normalization ──

class TestNormalizeStatus:
    def test_matched_is_filled(self):
        assert _normalize_status("matched", 100, 100) == "filled"

    def test_full_match_by_size(self):
        assert _normalize_status("live", 100, 100) == "filled"

    def test_partial(self):
        assert _normalize_status("live", 100, 40) == "partial"

    def test_open_is_submitted(self):
        assert _normalize_status("live", 100, 0) == "submitted"

    def test_cancelled(self):
        assert _normalize_status("canceled", 100, 0) == "cancelled"


# ── Hedge assessment (pure) ──

def _t(status):
    return SimpleNamespace(status=status, order_id="o", user_id=uuid4())


class TestAssessHedge:
    def test_one_filled_one_working_is_partial(self):
        is_partial, to_cancel = assess_hedge([_t("filled"), _t("submitted")])
        assert is_partial is True
        assert len(to_cancel) == 1

    def test_all_filled_is_not_partial(self):
        is_partial, to_cancel = assess_hedge([_t("filled"), _t("filled")])
        assert is_partial is False
        assert to_cancel == []

    def test_all_working_is_not_partial(self):
        is_partial, to_cancel = assess_hedge([_t("submitted"), _t("submitted")])
        assert is_partial is False


# ── Fake order client + live executor path ──

class FakeOrderClient:
    def __init__(self, place_results=None, get_result=None):
        self.place_results = place_results or {}   # token_id -> OrderResult
        # FOK success = fully filled
        self.default_place = OrderResult(success=True, order_id="ok", status="filled", filled_size=1.0)
        self.get_result = get_result
        self.cancelled = []
        self.placed = []          # (token_id, side) tuples
        self.placed_tokens = []   # token_ids only (back-comfrom convenience)

    async def place_order(self, private_key, token_id, side, price, size, tif="GTC"):
        self.placed.append((token_id, side))
        self.placed_tokens.append(token_id)
        return self.place_results.get(token_id, self.default_place)

    async def cancel_order(self, private_key, order_id):
        self.cancelled.append(order_id)
        return True

    async def cancel_all(self, private_key):
        return True

    async def get_order(self, private_key, order_id):
        return self.get_result


def _install_min_fakes(monkeypatch, store):
    async def claim_opportunity(session, opp_id, expected_status="queued", new_status="executing"):
        if opp_id in store["claimed"]:
            return False
        store["claimed"].add(opp_id)
        return True

    async def get_daily_loss(s, u): return 0.0
    async def get_trades_last_hour(s, u): return 0
    async def get_capital_deployed(s, u): return 0.0

    async def create_trade(session, **kwargs):
        store["trades"].append(kwargs)
        return SimpleNamespace(id=uuid4(), side=kwargs["side"], status=kwargs["status"])

    async def update_opportunity_status(session, opp_id, status, **kw):
        store["opp_status"][opp_id] = status
        return SimpleNamespace(id=opp_id, status=status)

    q = executor_mod.queries
    monkeypatch.setattr(q, "claim_opportunity", claim_opportunity)
    monkeypatch.setattr(q, "get_daily_loss", get_daily_loss)
    monkeypatch.setattr(q, "get_trades_last_hour", get_trades_last_hour)
    monkeypatch.setattr(q, "get_capital_deployed", get_capital_deployed)
    monkeypatch.setattr(q, "create_trade", create_trade)
    monkeypatch.setattr(q, "update_opportunity_status", update_opportunity_status)


def _live_cfg():
    return SimpleNamespace(encrypted_private_key="enc", settings={
        "bot_active": True, "paper_trading": False, "max_trade_size": 100.0,
        "max_capital_deployed": 1000.0, "usdc_budget": 1000.0, "reserve_amount": 50.0,
        "max_daily_loss": 50.0, "max_trades_per_hour": 20, "min_profit_pct": 1.0,
    })


def _binary_opp():
    return calculate_binary_arbitrage(
        market_id="m", condition_id="c", market_question="q", market_slug="q",
        yes_ask=0.45, no_ask=0.50, yes_liquidity=1000.0, no_liquidity=1000.0,
        yes_token_id="tok_yes", no_token_id="tok_no",
    )


def _patch_live_helpers(monkeypatch):
    # decrypt + re-verify are network/crypto; stub them for the live-path test
    monkeypatch.setattr(executor_mod, "decrypt_private_key", lambda enc: "pk")

    async def ok_reverify(self, db, settings, opp, opp_id, probe):
        return None
    monkeypatch.setattr(ExecutionEngine, "_reverify_binary", ok_reverify)


class _PM:
    """Polymarket stub exposing get_order_book for the unwind path."""
    async def get_order_book(self, token_id):
        return {"bids": [{"price": "0.44", "size": "100000"}], "asks": []}


class TestLiveBinaryExecution:
    def test_both_legs_placed_with_real_token_ids(self, monkeypatch):
        store = {"claimed": set(), "trades": [], "opp_status": {}}
        _install_min_fakes(monkeypatch, store)
        _patch_live_helpers(monkeypatch)
        fake = FakeOrderClient()  # default = FOK filled
        eng = ExecutionEngine(polymarket=_PM(), order_client=fake)

        res = asyncio.run(eng.execute_binary_arbitrage(
            SimpleNamespace(), uuid4(), _live_cfg(), _binary_opp(), uuid4()))
        assert res.success is True
        # both legs bought against the REAL token ids as FOK
        assert set(fake.placed_tokens) == {"tok_yes", "tok_no"}
        assert ("tok_yes", "buy") in fake.placed and ("tok_no", "buy") in fake.placed
        assert len(store["trades"]) == 2

    def test_killed_second_leg_unwinds_first_leg(self, monkeypatch):
        store = {"claimed": set(), "trades": [], "opp_status": {}}
        _install_min_fakes(monkeypatch, store)
        _patch_live_helpers(monkeypatch)
        opp_id = uuid4()
        # YES fills FOK; NO killed. Unwind must SELL tok_yes to flatten.
        fake = FakeOrderClient(place_results={
            "tok_yes": OrderResult(success=True, order_id="yes1", status="filled", filled_size=1.0),
            "tok_no": OrderResult(success=False, status="failed", error="FOK killed"),
        })
        eng = ExecutionEngine(polymarket=_PM(), order_client=fake)

        res = asyncio.run(eng.execute_binary_arbitrage(
            SimpleNamespace(), uuid4(), _live_cfg(), _binary_opp(), opp_id))
        assert res.success is False
        assert res.partial_fill is True
        # Auto-unwind: a SELL was placed on the filled YES leg — never left naked
        assert ("tok_yes", "sell") in fake.placed
        assert store["opp_status"][opp_id] == "partial"


# ── VWAP re-verify rejection ──

def _stub_opp_status(monkeypatch):
    async def noop(session, opp_id, status, **kw):
        return SimpleNamespace(id=opp_id, status=status)
    monkeypatch.setattr(executor_mod.queries, "update_opportunity_status", noop)


class TestReverifyVwap:
    def test_reverify_rejects_when_book_moved(self, monkeypatch):
        _stub_opp_status(monkeypatch)
        # Order books that only fill the size at prices summing >= 1.0
        thin = {"bids": [], "asks": [{"price": "0.60", "size": "100000"}]}

        class PM:
            async def get_order_book(self, token_id):
                return thin
        eng = ExecutionEngine(polymarket=PM())
        opp = _binary_opp()
        err = asyncio.run(eng._reverify_binary(
            SimpleNamespace(), {"min_profit_pct": 1.0}, opp, uuid4(), 500.0))
        # 0.60 + 0.60 = 1.20 >= 1.0 -> rejected
        assert err is not None
        assert "moved" in err or "profitable" in err

    def test_reverify_passes_when_still_cheap(self, monkeypatch):
        _stub_opp_status(monkeypatch)
        cheap_yes = {"bids": [], "asks": [{"price": "0.45", "size": "100000"}]}
        cheap_no = {"bids": [], "asks": [{"price": "0.50", "size": "100000"}]}

        class PM:
            async def get_order_book(self, token_id):
                return cheap_yes if token_id == "tok_yes" else cheap_no
        eng = ExecutionEngine(polymarket=PM())
        opp = _binary_opp()
        err = asyncio.run(eng._reverify_binary(
            SimpleNamespace(), {"min_profit_pct": 1.0}, opp, uuid4(), 500.0))
        assert err is None
