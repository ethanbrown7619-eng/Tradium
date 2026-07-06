"""
Phase 4 tests: settlement -> realized P&L.

Proves the resolution math and that a resolved market books realized P&L (which is
what makes the daily-loss stop real for live trades) and marks trades settled.
"""
import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.bot import settlement as settlement_mod
from app.bot.settlement import settle_position_pnl, _aggregate_positions, settle_resolved_positions


class TestSettlePositionPnl:
    def test_winning_token(self):
        # bought 100 @ 0.40, token wins (redeems $1) -> +$60
        assert settle_position_pnl(100, 0.40, 1.0) == pytest.approx(60.0)

    def test_losing_token(self):
        # bought 100 @ 0.40, token loses (redeems $0) -> -$40
        assert settle_position_pnl(100, 0.40, 0.0) == pytest.approx(-40.0)

    def test_aggregate_positions_average_cost(self):
        trades = [
            SimpleNamespace(token_id="t", filled_size=100, fill_price=0.40),
            SimpleNamespace(token_id="t", filled_size=100, fill_price=0.50),
        ]
        pos = _aggregate_positions(trades)
        assert pos["t"]["qty"] == pytest.approx(200)
        assert pos["t"]["avg_entry_price"] == pytest.approx(0.45)


def _trade(user_id, condition_id, token_id, qty, price):
    return SimpleNamespace(
        id=uuid4(), user_id=user_id, condition_id=condition_id, token_id=token_id,
        filled_size=qty, fill_price=price, market_id="m", opportunity_id=uuid4(),
    )


class TestSettleResolvedPositions:
    def test_books_realized_pnl_and_marks_settled(self, monkeypatch):
        uid = uuid4()
        cond = "0xcond"
        created = []
        settled_ids = []

        yes_trades = [_trade(uid, cond, "tok_yes", 100, 0.45)]
        no_trades = [_trade(uid, cond, "tok_no", 100, 0.50)]
        all_trades = yes_trades + no_trades

        async def get_unsettled(db, limit=1000): return all_trades
        async def create_trade(db, **kw): created.append(kw); return SimpleNamespace(id=uuid4())
        async def mark_settled(db, ids): settled_ids.extend(ids); return len(ids)

        monkeypatch.setattr(settlement_mod.queries, "get_unsettled_filled_trades", get_unsettled)
        monkeypatch.setattr(settlement_mod.queries, "create_trade", create_trade)
        monkeypatch.setattr(settlement_mod.queries, "mark_trades_settled", mark_settled)

        # YES wins
        async def resolve(condition_id):
            return {"resolved": True, "winning_token_id": "tok_yes"}

        stats = asyncio.run(settle_resolved_positions(SimpleNamespace(), resolve))

        # Two positions settled (YES, NO); all trades marked settled
        assert stats["settled_positions"] == 2
        assert len(settled_ids) == 2
        # Net realized: YES 100*(1-0.45)=+55 ; NO 100*(0-0.50)=-50 ; total +5
        assert stats["realized_pnl"] == pytest.approx(5.0)
        settlement_pnls = sorted(float(c["profit_loss"]) for c in created)
        assert settlement_pnls == pytest.approx([-50.0, 55.0])

    def test_unresolved_market_is_skipped(self, monkeypatch):
        uid = uuid4()
        async def get_unsettled(db, limit=1000):
            return [_trade(uid, "0xc", "tok", 100, 0.4)]
        async def create_trade(db, **kw): raise AssertionError("should not settle")
        async def mark_settled(db, ids): raise AssertionError("should not settle")
        monkeypatch.setattr(settlement_mod.queries, "get_unsettled_filled_trades", get_unsettled)
        monkeypatch.setattr(settlement_mod.queries, "create_trade", create_trade)
        monkeypatch.setattr(settlement_mod.queries, "mark_trades_settled", mark_settled)

        async def resolve(cid): return {"resolved": False, "winning_token_id": None}
        stats = asyncio.run(settle_resolved_positions(SimpleNamespace(), resolve))
        assert stats["settled_positions"] == 0
