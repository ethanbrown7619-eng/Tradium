"""
Phase 2 tests: declarative strategy engine.

Covers the safety-critical properties:
- A strategy referencing an unknown indicator is REJECTED at save-time validation
  (no eval/exec path can ever be stored).
- entry / exit / stop conditions fire correctly; stop takes priority over exit.
- max_open_positions blocks the N+1 entry.
- A strategy intent flows through the SAME executor claim+risk spine as arbitrage.
"""
import asyncio
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.strategy import StrategyDefinition
from app.bot.strategies import engine
from app.bot.strategies.engine import (
    StrategyContext, Position, StrategyIntent, evaluate, evaluate_strategy_for_market,
    build_token_map, INDICATOR_NAMES,
)
from app.bot import executor as executor_mod
from app.bot.executor import ExecutionEngine


# ── Save-time validation (the no-arbitrary-code gate) ──

class TestStrategyValidation:
    def test_valid_strategy_accepted(self):
        s = StrategyDefinition(
            name="cheap-yes",
            entry={"all": [
                {"cmp": {"lhs": {"name": "best_ask", "token": "YES"}, "op": "<", "rhs": 0.30}},
                {"cmp": {"lhs": {"name": "spread", "token": "YES"}, "op": "<", "rhs": 0.02}},
            ]},
        )
        assert s.name == "cheap-yes"

    def test_unknown_indicator_rejected(self):
        with pytest.raises(ValidationError):
            StrategyDefinition(
                name="evil",
                entry={"cmp": {"lhs": {"name": "__import__", "token": "YES"}, "op": "<", "rhs": 0.3}},
            )

    def test_unknown_operator_rejected(self):
        with pytest.raises(ValidationError):
            StrategyDefinition(
                name="bad-op",
                entry={"cmp": {"lhs": {"name": "best_ask"}, "op": "matches", "rhs": 0.3}},
            )

    def test_malformed_node_rejected(self):
        with pytest.raises(ValidationError):
            StrategyDefinition(name="bad", entry={"best_ask": 0.3})

    def test_requires_at_least_one_condition(self):
        with pytest.raises(ValidationError):
            StrategyDefinition(name="empty")

    def test_indicator_whitelist_has_no_dunder(self):
        assert all(not n.startswith("__") for n in INDICATOR_NAMES)

    def test_oversized_window_rejected(self):
        with pytest.raises(ValidationError):
            StrategyDefinition(
                name="cpu-bomb",
                entry={"cmp": {"lhs": {"name": "sma", "token": "YES", "window": 10_000_000}, "op": ">", "rhs": 0.1}},
            )

    def test_oversized_vwap_size_rejected(self):
        with pytest.raises(ValidationError):
            StrategyDefinition(
                name="big-probe",
                entry={"cmp": {"lhs": {"name": "vwap_at", "token": "YES", "size": 1e12}, "op": "<", "rhs": 0.5}},
            )

    def test_nan_literal_rejected(self):
        with pytest.raises(ValidationError):
            StrategyDefinition(
                name="nan",
                entry={"cmp": {"lhs": {"name": "best_ask"}, "op": "<", "rhs": float("inf")}},
            )

    def test_deeply_nested_tree_rejected(self):
        node = {"cmp": {"lhs": {"name": "best_ask"}, "op": "<", "rhs": 0.5}}
        for _ in range(30):  # exceed MAX_CONDITION_DEPTH
            node = {"all": [node]}
        with pytest.raises(ValidationError):
            StrategyDefinition(name="deep", entry=node)


# ── Engine helpers ──

def _book(ask, bid, size=5000):
    return {"asks": [{"price": str(ask), "size": str(size)}],
            "bids": [{"price": str(bid), "size": str(size)}]}


def _market():
    return {
        "market_id": "m", "condition_id": "c", "question": "q", "slug": "s",
        "tokens": [{"token_id": "tok_yes", "outcome": "YES"},
                   {"token_id": "tok_no", "outcome": "NO"}],
    }


def _ctx(book, positions=None):
    m = _market()
    return StrategyContext(
        market=m, order_books={"tok_yes": book}, token_map=build_token_map(m),
        positions=positions or {},
    )


class TestEvaluate:
    def test_and_condition(self):
        ctx = _ctx(_book(0.25, 0.24))  # best_ask 0.25, spread 0.01
        cond = {"all": [
            {"cmp": {"lhs": {"name": "best_ask", "token": "YES"}, "op": "<", "rhs": 0.30}},
            {"cmp": {"lhs": {"name": "spread", "token": "YES"}, "op": "<", "rhs": 0.02}},
        ]}
        assert evaluate(cond, ctx) is True

    def test_insufficient_data_is_false_not_error(self):
        # sma with no history -> None -> comparison False, never raises
        ctx = _ctx(_book(0.25, 0.24))
        cond = {"cmp": {"lhs": {"name": "sma", "token": "YES", "window": 5}, "op": ">", "rhs": 0.1}}
        assert evaluate(cond, ctx) is False

    def test_unknown_op_is_false(self):
        ctx = _ctx(_book(0.25, 0.24))
        assert evaluate({"cmp": {"lhs": 1, "op": "??", "rhs": 2}}, ctx) is False


def _strategy(**over):
    base = {
        "id": str(uuid4()),
        "token": "YES",
        "sizing": {"mode": "fixed_usdc", "value": 50.0, "max_position_usdc": 100.0},
        "order": {"type": "marketable_limit", "limit_offset_bps": 0.0, "tif": "GTC"},
        "max_open_positions": 1,
    }
    base.update(over)
    return base


class TestEvaluateStrategyForMarket:
    def test_entry_emits_buy(self):
        book = _book(0.25, 0.24)
        ctx = _ctx(book)
        strat = _strategy(entry={"all": [
            {"cmp": {"lhs": {"name": "best_ask", "token": "YES"}, "op": "<", "rhs": 0.30}},
            {"cmp": {"lhs": {"name": "spread", "token": "YES"}, "op": "<", "rhs": 0.02}},
        ]})
        intent = evaluate_strategy_for_market(strat, _market(), ctx, budget=1000.0, open_position_count=0)
        assert intent is not None
        assert intent.side == "buy" and intent.reason == "entry"
        assert intent.target_qty == pytest.approx(50.0 / 0.25)  # $50 / price

    def test_exit_emits_sell_when_in_profit(self):
        book = _book(0.26, 0.24)  # mid 0.25
        pos = {"tok_yes": Position("tok_yes", qty=200, avg_entry_price=0.20)}  # +25%
        ctx = _ctx(book, pos)
        strat = _strategy(exit={"cmp": {"lhs": {"name": "unrealized_pnl_pct", "token": "YES"}, "op": ">", "rhs": 10}})
        intent = evaluate_strategy_for_market(strat, _market(), ctx, budget=1000.0, open_position_count=1)
        assert intent is not None
        assert intent.side == "sell" and intent.reason == "exit"
        assert intent.target_qty == pytest.approx(200)

    def test_stop_takes_priority_over_exit(self):
        book = _book(0.16, 0.14)  # mid 0.15 vs entry 0.20 -> -25%
        pos = {"tok_yes": Position("tok_yes", qty=200, avg_entry_price=0.20)}
        ctx = _ctx(book, pos)
        strat = _strategy(
            exit={"cmp": {"lhs": {"name": "unrealized_pnl_pct", "token": "YES"}, "op": ">", "rhs": 10}},
            stop={"cmp": {"lhs": {"name": "unrealized_pnl_pct", "token": "YES"}, "op": "<", "rhs": -10}},
        )
        intent = evaluate_strategy_for_market(strat, _market(), ctx, budget=1000.0, open_position_count=1)
        assert intent is not None
        assert intent.reason == "stop"

    def test_max_open_positions_blocks_entry(self):
        book = _book(0.25, 0.24)
        ctx = _ctx(book)
        strat = _strategy(
            max_open_positions=1,
            entry={"cmp": {"lhs": {"name": "best_ask", "token": "YES"}, "op": "<", "rhs": 0.30}},
        )
        # Already at the cap -> no new entry
        intent = evaluate_strategy_for_market(strat, _market(), ctx, budget=1000.0, open_position_count=1)
        assert intent is None


# ── Executor path (reuses the arb claim + risk spine) ──

def _install_exec_fakes(monkeypatch, store):
    async def claim_opportunity(session, opp_id, expected_status="queued", new_status="executing"):
        if opp_id in store["claimed"]:
            return False
        store["claimed"].add(opp_id)
        return True

    async def get_daily_loss(session, user_id): return 0.0
    async def get_trades_last_hour(session, user_id): return 0
    async def get_capital_deployed(session, user_id): return 0.0
    async def get_open_positions(session, user_id, strategy_id=None): return store["positions"]

    async def create_trade(session, **kwargs):
        store["trades"].append(kwargs)
        return SimpleNamespace(id=uuid4(), side=kwargs["side"], status=kwargs["status"])

    async def update_opportunity_status(session, opp_id, status, **kwargs):
        store["opp_status"][opp_id] = status
        return SimpleNamespace(id=opp_id, status=status)

    q = executor_mod.queries
    monkeypatch.setattr(q, "claim_opportunity", claim_opportunity)
    monkeypatch.setattr(q, "get_daily_loss", get_daily_loss)
    monkeypatch.setattr(q, "get_trades_last_hour", get_trades_last_hour)
    monkeypatch.setattr(q, "get_capital_deployed", get_capital_deployed)
    monkeypatch.setattr(q, "get_open_positions", get_open_positions)
    monkeypatch.setattr(q, "create_trade", create_trade)
    monkeypatch.setattr(q, "update_opportunity_status", update_opportunity_status)


def _paper_cfg():
    return SimpleNamespace(encrypted_private_key=None, settings={
        "bot_active": True, "paper_trading": True, "max_trade_size": 100.0,
        "max_capital_deployed": 1000.0, "usdc_budget": 1000.0, "reserve_amount": 50.0,
        "max_daily_loss": 50.0, "max_trades_per_hour": 20,
    })


class TestExecuteStrategyIntent:
    def test_paper_buy_records_position_opening_trade(self, monkeypatch):
        store = {"claimed": set(), "trades": [], "opp_status": {}, "positions": {}}
        _install_exec_fakes(monkeypatch, store)
        eng = ExecutionEngine(polymarket=SimpleNamespace())
        intent = StrategyIntent(str(uuid4()), "m", "c", "tok", "YES", "buy", 200.0, 0.25, "entry")
        opp_id = uuid4()

        res = asyncio.run(eng.execute_strategy_intent(SimpleNamespace(), uuid4(), _paper_cfg(), intent, opp_id))
        assert res.success is True
        t = store["trades"][0]
        assert t["strategy_type"] == "strategy"
        assert t["side"] == "buy:YES"
        assert float(t["filled_size"]) == pytest.approx(200.0)   # positive = opening
        assert float(t["size_usdc"]) == pytest.approx(50.0)

    def test_paper_sell_realizes_pnl_against_avg_entry(self, monkeypatch):
        store = {"claimed": set(), "trades": [], "opp_status": {},
                 "positions": {"tok": {"qty": 200.0, "avg_entry_price": 0.20}}}
        _install_exec_fakes(monkeypatch, store)
        eng = ExecutionEngine(polymarket=SimpleNamespace())
        intent = StrategyIntent(str(uuid4()), "m", "c", "tok", "YES", "sell", 200.0, 0.25, "exit")
        opp_id = uuid4()

        res = asyncio.run(eng.execute_strategy_intent(SimpleNamespace(), uuid4(), _paper_cfg(), intent, opp_id))
        assert res.success is True
        t = store["trades"][0]
        assert float(t["filled_size"]) == pytest.approx(-200.0)   # negative = closing
        # realized = (0.25 - 0.20) * 200 = 10.0
        assert float(t["profit_loss"]) == pytest.approx(10.0)

    def test_sell_clamps_to_held_position(self, monkeypatch):
        # Strategy asks to sell 500 but only 200 held -> clamp to 200
        store = {"claimed": set(), "trades": [], "opp_status": {},
                 "positions": {"tok": {"qty": 200.0, "avg_entry_price": 0.20}}}
        _install_exec_fakes(monkeypatch, store)
        eng = ExecutionEngine(polymarket=SimpleNamespace())
        intent = StrategyIntent(str(uuid4()), "m", "c", "tok", "YES", "sell", 500.0, 0.25, "exit")
        res = asyncio.run(eng.execute_strategy_intent(SimpleNamespace(), uuid4(), _paper_cfg(), intent, uuid4()))
        assert res.success is True
        t = store["trades"][0]
        assert float(t["filled_size"]) == pytest.approx(-200.0)   # clamped, not -500
        # realized only on the 200 actually held
        assert float(t["profit_loss"]) == pytest.approx((0.25 - 0.20) * 200)

    def test_sell_with_no_position_is_rejected(self, monkeypatch):
        store = {"claimed": set(), "trades": [], "opp_status": {}, "positions": {}}
        _install_exec_fakes(monkeypatch, store)
        eng = ExecutionEngine(polymarket=SimpleNamespace())
        intent = StrategyIntent(str(uuid4()), "m", "c", "tok", "YES", "sell", 200.0, 0.25, "exit")
        opp_id = uuid4()
        res = asyncio.run(eng.execute_strategy_intent(SimpleNamespace(), uuid4(), _paper_cfg(), intent, opp_id))
        assert res.success is False
        assert len(store["trades"]) == 0
        assert "No position" in res.error

    def test_global_open_position_cap_blocks_new_buy(self, monkeypatch):
        positions = {f"t{i}": {"qty": 1.0, "avg_entry_price": 0.5} for i in range(20)}  # at cap (20)
        store = {"claimed": set(), "trades": [], "opp_status": {}, "positions": positions}
        _install_exec_fakes(monkeypatch, store)
        eng = ExecutionEngine(polymarket=SimpleNamespace())
        intent = StrategyIntent(str(uuid4()), "m", "c", "tokNEW", "YES", "buy", 100.0, 0.25, "entry")
        res = asyncio.run(eng.execute_strategy_intent(SimpleNamespace(), uuid4(), _paper_cfg(), intent, uuid4()))
        assert res.success is False
        assert "cap" in res.error.lower()
        assert len(store["trades"]) == 0

    def test_per_token_notional_cap_blocks_buy(self, monkeypatch):
        # token already at $200 notional (the default per-position cap)
        store = {"claimed": set(), "trades": [], "opp_status": {},
                 "positions": {"tok": {"qty": 400.0, "avg_entry_price": 0.5}}}
        _install_exec_fakes(monkeypatch, store)
        eng = ExecutionEngine(polymarket=SimpleNamespace())
        intent = StrategyIntent(str(uuid4()), "m", "c", "tok", "YES", "buy", 200.0, 0.25, "entry")
        res = asyncio.run(eng.execute_strategy_intent(SimpleNamespace(), uuid4(), _paper_cfg(), intent, uuid4()))
        assert res.success is False
        assert "cap" in res.error.lower()

    def test_non_crossing_limit_rests_unfilled(self, monkeypatch):
        # A buy limit that does not cross the book must NOT book a fill in paper
        store = {"claimed": set(), "trades": [], "opp_status": {}, "positions": {}}
        _install_exec_fakes(monkeypatch, store)
        eng = ExecutionEngine(polymarket=SimpleNamespace())
        intent = StrategyIntent(str(uuid4()), "m", "c", "tok", "YES", "buy", 200.0, 0.25,
                                "entry", est_fill_price=0.25, crosses=False)
        res = asyncio.run(eng.execute_strategy_intent(SimpleNamespace(), uuid4(), _paper_cfg(), intent, uuid4()))
        assert res.success is True
        t = store["trades"][0]
        assert float(t["filled_size"]) == 0.0
        assert t["status"] == "open"

    def test_crossing_limit_fills_at_vwap_not_limit(self, monkeypatch):
        # Crossing buy fills at the VWAP estimate, which is better than the limit
        store = {"claimed": set(), "trades": [], "opp_status": {}, "positions": {}}
        _install_exec_fakes(monkeypatch, store)
        eng = ExecutionEngine(polymarket=SimpleNamespace())
        intent = StrategyIntent(str(uuid4()), "m", "c", "tok", "YES", "buy", 200.0, 0.40,
                                "entry", est_fill_price=0.30, crosses=True)
        res = asyncio.run(eng.execute_strategy_intent(SimpleNamespace(), uuid4(), _paper_cfg(), intent, uuid4()))
        assert res.success is True
        t = store["trades"][0]
        assert float(t["fill_price"]) == pytest.approx(0.30)  # VWAP, not the 0.40 limit

    def test_claim_prevents_double_execution(self, monkeypatch):
        store = {"claimed": set(), "trades": [], "opp_status": {}, "positions": {}}
        _install_exec_fakes(monkeypatch, store)
        eng = ExecutionEngine(polymarket=SimpleNamespace())
        intent = StrategyIntent(str(uuid4()), "m", "c", "tok", "YES", "buy", 200.0, 0.25, "entry")
        opp_id = uuid4()
        cfg = _paper_cfg()
        uid = uuid4()

        async def run_two():
            return await asyncio.gather(
                eng.execute_strategy_intent(SimpleNamespace(), uid, cfg, intent, opp_id),
                eng.execute_strategy_intent(SimpleNamespace(), uid, cfg, intent, opp_id),
            )
        results = asyncio.run(run_two())
        assert len([r for r in results if r.success]) == 1
        assert len(store["trades"]) == 1
