"""
Tests for the kill switch — it must actually cancel orders and halt, not flip a flag.
"""
import asyncio
from types import SimpleNamespace
from uuid import uuid4

from app.bot import safety as safety_mod
from app.bot.safety import engage_kill_switch


class FakeClient:
    def __init__(self):
        self.cancel_all_called = False

    async def cancel_all(self, private_key):
        self.cancel_all_called = True
        return True


def test_kill_switch_cancels_orders_trades_opportunities_and_halts(monkeypatch):
    captured = {}

    async def cancel_open_trades(db, user_id):
        return 3

    async def cancel_open_opportunities(db, user_id):
        return 2

    async def update_user_config(db, user_id, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(user_id=user_id, **kwargs)

    monkeypatch.setattr(safety_mod.queries, "cancel_open_trades", cancel_open_trades)
    monkeypatch.setattr(safety_mod.queries, "cancel_open_opportunities", cancel_open_opportunities)
    monkeypatch.setattr(safety_mod.queries, "update_user_config", update_user_config)
    monkeypatch.setattr(safety_mod, "decrypt_private_key", lambda enc: "pk")

    fake = FakeClient()
    config = SimpleNamespace(
        user_id=uuid4(), encrypted_private_key="enc",
        settings={"bot_active": True, "notify_email": None, "notify_on_kill_switch": True},
    )

    result = asyncio.run(engage_kill_switch(SimpleNamespace(), config, order_client=fake))

    assert fake.cancel_all_called is True            # venue orders nuked
    assert result["trades_cancelled"] == 3
    assert result["opportunities_cancelled"] == 2
    assert captured["settings"]["bot_active"] is False   # bot halted
    assert "kill_switch_activated_at" in captured        # event stamped


def test_kill_switch_without_wallet_still_halts(monkeypatch):
    captured = {}

    async def cancel_open_trades(db, user_id): return 0
    async def cancel_open_opportunities(db, user_id): return 0
    async def update_user_config(db, user_id, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(safety_mod.queries, "cancel_open_trades", cancel_open_trades)
    monkeypatch.setattr(safety_mod.queries, "cancel_open_opportunities", cancel_open_opportunities)
    monkeypatch.setattr(safety_mod.queries, "update_user_config", update_user_config)

    config = SimpleNamespace(
        user_id=uuid4(), encrypted_private_key=None,  # no wallet
        settings={"bot_active": True, "notify_email": None},
    )
    result = asyncio.run(engage_kill_switch(SimpleNamespace(), config))
    assert result["orders_cancelled"] is False
    assert captured["settings"]["bot_active"] is False
