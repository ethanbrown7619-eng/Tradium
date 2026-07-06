"""
Tests for the execution engine — the money-moving layer.

Focus (Phase 0):
- compute_leg_sizes buys EQUAL SHARES per leg (the arbitrage hedge invariant).
- Recorded P&L is dollar-denominated, not a per-share fraction.
- The atomic opportunity claim prevents double-execution under concurrent scans.

These tests use in-memory fakes for the DB layer (monkeypatched onto
app.database.queries) so they need no Postgres and no async pytest plugin —
each async scenario is driven with asyncio.run().
"""
import asyncio
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.bot import executor as executor_mod
from app.bot.executor import ExecutionEngine, compute_leg_sizes, _arb_pnl
from app.bot.strategies.binary import calculate_binary_arbitrage
from app.bot.strategies.multioutcome import calculate_multi_outcome_arbitrage


# ── Pure sizing / accounting math ──

class TestComputeLegSizes:
    def test_equal_shares_across_legs(self):
        shares, per_leg = compute_leg_sizes({"YES": 0.45, "NO": 0.52}, 100.0)
        # shares = 100 / 0.97
        assert shares == pytest.approx(100.0 / 0.97)
        # Cost is price-weighted, but SHARE COUNT is identical on both legs
        assert per_leg["YES"] == pytest.approx(shares * 0.45)
        assert per_leg["NO"] == pytest.approx(shares * 0.52)
        # Total cost never exceeds the target notional
        assert sum(per_leg.values()) == pytest.approx(100.0)

    def test_zero_notional(self):
        shares, per_leg = compute_leg_sizes({"YES": 0.45, "NO": 0.52}, 0.0)
        assert shares == 0.0
        assert per_leg == {"YES": 0.0, "NO": 0.0}

    def test_multi_outcome_equal_shares(self):
        shares, per_leg = compute_leg_sizes({"A": 0.30, "B": 0.25, "C": 0.20}, 150.0)
        assert shares == pytest.approx(150.0 / 0.75)
        assert sum(per_leg.values()) == pytest.approx(150.0)

    def test_arb_pnl_is_dollars(self):
        # YES=0.45/NO=0.52: gross/share=0.03, worst fee/share=0.011, gas=0.01
        shares, _ = compute_leg_sizes({"YES": 0.45, "NO": 0.52}, 100.0)
        total_cost, total_fee, total_net = _arb_pnl(shares, 0.97, 0.011, 0.01)
        assert total_cost == pytest.approx(100.0)
        assert total_fee == pytest.approx(shares * 0.011)
        # ~$1.95 on $100 deployed — a real dollar figure, not $0.009
        assert total_net == pytest.approx(shares * (0.03 - 0.011) - 0.01)
        assert total_net > 1.0


# ── Fake DB layer for executor path tests ──

class FakeStore:
    def __init__(self):
        self.claimed: set = set()
        self.trades: list[dict] = []
        self.opp_status: dict = {}
        self.daily_loss = 0.0
        self.trades_last_hour = 0
        self.capital_deployed = 0.0


def install_fakes(monkeypatch, store: FakeStore):
    async def claim_opportunity(session, opp_id, expected_status="queued", new_status="executing"):
        # Atomic winner-takes-all: only the first caller for an opp_id wins.
        if opp_id in store.claimed:
            return False
        store.claimed.add(opp_id)
        return True

    async def get_daily_loss(session, user_id):
        return store.daily_loss

    async def get_trades_last_hour(session, user_id):
        return store.trades_last_hour

    async def get_capital_deployed(session, user_id):
        return store.capital_deployed

    async def create_trade(session, **kwargs):
        store.trades.append(kwargs)
        return SimpleNamespace(
            id=uuid4(), side=kwargs["side"], status=kwargs["status"],
            order_id=kwargs.get("order_id"),
        )

    async def update_opportunity_status(session, opp_id, status, **kwargs):
        store.opp_status[opp_id] = status
        return SimpleNamespace(id=opp_id, status=status)

    q = executor_mod.queries
    monkeypatch.setattr(q, "claim_opportunity", claim_opportunity)
    monkeypatch.setattr(q, "get_daily_loss", get_daily_loss)
    monkeypatch.setattr(q, "get_trades_last_hour", get_trades_last_hour)
    monkeypatch.setattr(q, "get_capital_deployed", get_capital_deployed)
    monkeypatch.setattr(q, "create_trade", create_trade)
    monkeypatch.setattr(q, "update_opportunity_status", update_opportunity_status)


def _paper_config():
    return SimpleNamespace(
        encrypted_private_key=None,
        settings={
            "bot_active": True,
            "paper_trading": True,
            "max_trade_size": 100.0,
            "max_capital_deployed": 1000.0,
            "usdc_budget": 1000.0,
            "reserve_amount": 50.0,
            "max_daily_loss": 50.0,
            "max_trades_per_hour": 20,
            "min_profit_pct": 1.0,
        },
    )


def _binary_opp():
    # min_liquidity = 1000 so target_notional = min(100, 1000) = 100
    return calculate_binary_arbitrage(
        market_id="m1", condition_id="c1", market_question="Q?", market_slug="q",
        yes_ask=0.45, no_ask=0.52, yes_liquidity=1000.0, no_liquidity=1000.0,
    )


class TestBinaryExecution:
    def test_paper_execution_buys_equal_shares_and_dollar_pnl(self, monkeypatch):
        store = FakeStore()
        install_fakes(monkeypatch, store)
        engine = ExecutionEngine(polymarket=SimpleNamespace())
        opp = _binary_opp()
        opp_id = uuid4()

        result = asyncio.run(engine.execute_binary_arbitrage(
            db=SimpleNamespace(), user_id=uuid4(), config=_paper_config(),
            opportunity=opp, opportunity_id=opp_id,
        ))

        assert result.success is True
        assert len(store.trades) == 2

        yes = next(t for t in store.trades if t["side"] == "YES")
        no = next(t for t in store.trades if t["side"] == "NO")

        # EQUAL SHARES on both legs — the hedge invariant
        assert float(yes["filled_size"]) == pytest.approx(float(no["filled_size"]))
        assert float(yes["filled_size"]) == pytest.approx(100.0 / 0.97)

        # Total cost <= notional
        total_cost = float(yes["size_usdc"]) + float(no["size_usdc"])
        assert total_cost == pytest.approx(100.0)
        assert total_cost <= 100.0 + 1e-6

        # P&L is dollars (~$1.95), NOT a per-share $0.009
        total_pnl = float(yes["profit_loss"]) + float(no["profit_loss"])
        assert total_pnl > 1.0
        assert store.opp_status[opp_id] == "executed"

    def test_concurrent_execution_claims_once(self, monkeypatch):
        store = FakeStore()
        install_fakes(monkeypatch, store)
        engine = ExecutionEngine(polymarket=SimpleNamespace())
        opp = _binary_opp()
        opp_id = uuid4()
        cfg = _paper_config()
        uid = uuid4()

        async def run_two():
            return await asyncio.gather(
                engine.execute_binary_arbitrage(SimpleNamespace(), uid, cfg, opp, opp_id),
                engine.execute_binary_arbitrage(SimpleNamespace(), uid, cfg, opp, opp_id),
            )

        results = asyncio.run(run_two())
        successes = [r for r in results if r.success]

        # Exactly one execution wins the claim; the other is skipped
        assert len(successes) == 1
        assert len(store.trades) == 2  # only the winner's two legs
        assert any("already claimed" in (r.error or "") for r in results if not r.success)

    def test_kill_switch_blocks_execution(self, monkeypatch):
        store = FakeStore()
        install_fakes(monkeypatch, store)
        engine = ExecutionEngine(polymarket=SimpleNamespace())
        cfg = _paper_config()
        cfg.settings["bot_active"] = False  # kill switch engaged
        opp = _binary_opp()
        opp_id = uuid4()

        result = asyncio.run(engine.execute_binary_arbitrage(
            SimpleNamespace(), uuid4(), cfg, opp, opp_id,
        ))
        assert result.success is False
        assert len(store.trades) == 0
        assert "not active" in result.error


class TestMultiOutcomeExecution:
    def test_paper_execution_equal_shares(self, monkeypatch):
        store = FakeStore()
        install_fakes(monkeypatch, store)
        engine = ExecutionEngine(polymarket=SimpleNamespace())
        opp = calculate_multi_outcome_arbitrage(
            market_id="m2", condition_id="c2", market_question="Who?", market_slug="who",
            outcome_prices={"A": 0.30, "B": 0.25, "C": 0.20},
            outcome_liquidities={"A": 1000.0, "B": 1000.0, "C": 1000.0},
        )
        opp_id = uuid4()

        result = asyncio.run(engine.execute_multi_outcome_arbitrage(
            SimpleNamespace(), uuid4(), _paper_config(), opp, opp_id,
        ))

        assert result.success is True
        assert len(store.trades) == 3
        share_counts = [float(t["filled_size"]) for t in store.trades]
        # All legs hold the same share count
        assert max(share_counts) == pytest.approx(min(share_counts))
        total_pnl = sum(float(t["profit_loss"]) for t in store.trades)
        assert total_pnl > 1.0
