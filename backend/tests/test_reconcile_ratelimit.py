"""
Phase 4 tests: orphan-order reconciliation and the API rate limiter.
"""
import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.bot import reconcile as reconcile_mod
from app.bot.reconcile import reconcile_orphan_orders
from app.services.ratelimit import AsyncRateLimiter


class FakeClient:
    def __init__(self, open_orders):
        self._open = open_orders
        self.cancelled = []

    async def get_open_orders(self, pk):
        return self._open

    async def cancel_order(self, pk, order_id):
        self.cancelled.append(order_id)
        return True


class TestReconcile:
    def test_orphan_cancelled_known_kept(self, monkeypatch):
        uid = uuid4()

        async def get_wallet_user_ids(db):
            return [uid]

        async def get_known_order_ids(db, user_id):
            return {"known1"}  # DB knows order "known1"

        monkeypatch.setattr(reconcile_mod.queries, "get_wallet_user_ids", get_wallet_user_ids)
        monkeypatch.setattr(reconcile_mod.queries, "get_known_order_ids", get_known_order_ids)

        # Venue reports a known order and an orphan the DB never recorded
        fake = FakeClient([{"order_id": "known1"}, {"order_id": "orphan9"}])

        async def resolve_key(user_id):
            return "pk"

        stats = asyncio.run(reconcile_orphan_orders(SimpleNamespace(), fake, resolve_key))
        assert stats["orphans_cancelled"] == 1
        assert fake.cancelled == ["orphan9"]  # only the orphan; known order untouched

    def test_no_key_skips_user(self, monkeypatch):
        uid = uuid4()
        async def get_wallet_user_ids(db): return [uid]
        async def get_known_order_ids(db, user_id): return set()
        monkeypatch.setattr(reconcile_mod.queries, "get_wallet_user_ids", get_wallet_user_ids)
        monkeypatch.setattr(reconcile_mod.queries, "get_known_order_ids", get_known_order_ids)
        fake = FakeClient([{"order_id": "x"}])

        async def resolve_key(user_id):
            return None  # can't auth -> skip
        stats = asyncio.run(reconcile_orphan_orders(SimpleNamespace(), fake, resolve_key))
        assert stats["users"] == 0
        assert fake.cancelled == []


class TestRateLimiter:
    def test_allows_up_to_max_without_waiting(self):
        t = [0.0]
        slept = []

        async def fake_sleep(d):
            slept.append(d)
            t[0] += d

        rl = AsyncRateLimiter(max_calls=3, period=1.0, clock=lambda: t[0], sleep=fake_sleep)

        async def run():
            for _ in range(3):
                await rl.acquire()

        asyncio.run(run())
        assert slept == []  # 3 calls within the window, no wait

    def test_fourth_call_waits_for_window(self):
        t = [0.0]
        slept = []

        async def fake_sleep(d):
            slept.append(d)
            t[0] += d

        rl = AsyncRateLimiter(max_calls=3, period=1.0, clock=lambda: t[0], sleep=fake_sleep)

        async def run():
            for _ in range(4):  # 4th exceeds the 3/period budget
                await rl.acquire()

        asyncio.run(run())
        assert len(slept) == 1
        assert slept[0] == pytest.approx(1.0)
