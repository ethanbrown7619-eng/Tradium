"""
Declarative strategy engine (Strategy mode — goal B).

A user strategy is DATA, never code. A strategy is a JSON document with a
whitelisted set of indicators and a closed boolean-condition AST. This module
evaluates that AST against live market/position context to emit buy/sell intents.

SECURITY INVARIANT: there is no eval(), no exec(), no getattr on user input, and
no dynamic import. Every indicator must be a key in the INDICATORS registry; any
unknown name is rejected at strategy-save time (see models/strategy.py, which
validates against INDICATOR_NAMES) and, defensively, evaluates to "insufficient
data" (None -> condition False) at runtime. The interpreter only ever walks a
fixed grammar of all/any/not/cmp nodes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

from app.services.polymarket import PolymarketService

logger = logging.getLogger(__name__)

# Comparison operators the `cmp` node may use — a closed set, no eval.
_OPS: dict[str, Callable[[float, float], bool]] = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


@dataclass
class Position:
    """A held position in a single token."""
    token_id: str
    qty: float = 0.0
    avg_entry_price: float = 0.0


@dataclass
class StrategyContext:
    """Everything an indicator may read. No access to anything outside this."""
    market: dict                                   # normalized market
    order_books: dict                              # token_id -> order book
    token_map: dict                                # outcome(upper) -> token_id
    positions: dict = field(default_factory=dict)  # token_id -> Position
    history: dict = field(default_factory=dict)    # token_id -> list[float] price series

    def resolve_token_id(self, token_ref: Optional[str]) -> Optional[str]:
        """Resolve a spec's 'token' (YES/NO/outcome/token_id) to a real token_id."""
        if token_ref is None:
            # Default to the first token in the market
            toks = self.market.get("tokens", [])
            return toks[0]["token_id"] if toks else None
        ref = str(token_ref)
        if ref in self.order_books:  # already a token_id
            return ref
        return self.token_map.get(ref.upper())

    def book_for(self, token_ref: Optional[str]) -> Optional[dict]:
        tid = self.resolve_token_id(token_ref)
        return self.order_books.get(tid) if tid else None


# ── Indicator registry (the whitelist) ──
# Each indicator is a pure function (spec, ctx) -> float | None.
# Returning None means "insufficient data" and makes any comparison using it False.

def _ind_best_ask(spec, ctx):
    return PolymarketService.get_best_ask_price(ctx.book_for(spec.get("token")))


def _ind_best_bid(spec, ctx):
    return PolymarketService.get_best_bid_price(ctx.book_for(spec.get("token")))


def _ind_mid(spec, ctx):
    book = ctx.book_for(spec.get("token"))
    ask = PolymarketService.get_best_ask_price(book)
    bid = PolymarketService.get_best_bid_price(book)
    if ask is None or bid is None:
        return None
    return (ask + bid) / 2.0


def _ind_spread(spec, ctx):
    book = ctx.book_for(spec.get("token"))
    ask = PolymarketService.get_best_ask_price(book)
    bid = PolymarketService.get_best_bid_price(book)
    if ask is None or bid is None:
        return None
    return ask - bid


def _ind_vwap_at(spec, ctx):
    size = float(spec.get("size", 100))
    return PolymarketService.get_fillable_price(ctx.book_for(spec.get("token")), "buy", size)


def _ind_volume_24h(spec, ctx):
    v = ctx.market.get("volume")
    return float(v) if v not in (None, "") else None


def _ind_position_qty(spec, ctx):
    tid = ctx.resolve_token_id(spec.get("token"))
    pos = ctx.positions.get(tid) if tid else None
    return pos.qty if pos else 0.0


def _ind_unrealized_pnl_pct(spec, ctx):
    tid = ctx.resolve_token_id(spec.get("token"))
    pos = ctx.positions.get(tid) if tid else None
    if not pos or pos.qty == 0 or pos.avg_entry_price <= 0:
        return None
    mid = _ind_mid(spec, ctx)
    if mid is None:
        return None
    return (mid - pos.avg_entry_price) / pos.avg_entry_price * 100.0


def _series(spec, ctx):
    tid = ctx.resolve_token_id(spec.get("token"))
    return ctx.history.get(tid, []) if tid else []


def _ind_sma(spec, ctx):
    window = int(spec.get("window", 5))
    s = _series(spec, ctx)
    if len(s) < window or window <= 0:
        return None  # cold start: not enough history -> condition False, never errors
    return sum(s[-window:]) / window


def _ind_price_change_pct(spec, ctx):
    window = int(spec.get("window", 5))
    s = _series(spec, ctx)
    if len(s) < window or window <= 0:
        return None
    first, last = s[-window], s[-1]
    if first <= 0:
        return None
    return (last - first) / first * 100.0


INDICATORS: dict[str, Callable[[dict, StrategyContext], Optional[float]]] = {
    "best_ask": _ind_best_ask,
    "best_bid": _ind_best_bid,
    "mid": _ind_mid,
    "spread": _ind_spread,
    "vwap_at": _ind_vwap_at,
    "volume_24h": _ind_volume_24h,
    "position_qty": _ind_position_qty,
    "unrealized_pnl_pct": _ind_unrealized_pnl_pct,
    "sma": _ind_sma,
    "price_change_pct": _ind_price_change_pct,
}

# Exposed to the Pydantic model so save-time validation rejects unknown names.
INDICATOR_NAMES = frozenset(INDICATORS.keys())
OPERATORS = frozenset(_OPS.keys())


def _operand_value(operand, ctx: StrategyContext) -> Optional[float]:
    """Resolve a cmp operand: either a literal number or an indicator spec."""
    if isinstance(operand, (int, float)):
        return float(operand)
    if isinstance(operand, dict) and "name" in operand:
        fn = INDICATORS.get(operand["name"])
        if fn is None:
            logger.warning(f"Unknown indicator at runtime: {operand.get('name')}")
            return None
        try:
            return fn(operand, ctx)
        except Exception as e:  # never let one indicator crash a scan
            logger.warning(f"Indicator {operand.get('name')} raised: {e}")
            return None
    return None


def evaluate(condition: Optional[dict], ctx: StrategyContext) -> bool:
    """
    Evaluate a condition AST against ctx. Grammar (closed):
        {"all": [Condition, ...]}     -> AND
        {"any": [Condition, ...]}     -> OR
        {"not": Condition}            -> NOT
        {"cmp": {"lhs": Operand, "op": OP, "rhs": Operand}}
    Operand = number | {"name": indicator, "token"?: ..., "window"?/"size"?: ...}
    A missing operand value (insufficient data) makes the cmp False.
    """
    if not condition:
        return False
    if "all" in condition:
        return all(evaluate(c, ctx) for c in condition["all"])
    if "any" in condition:
        return any(evaluate(c, ctx) for c in condition["any"])
    if "not" in condition:
        return not evaluate(condition["not"], ctx)
    if "cmp" in condition:
        cmp = condition["cmp"]
        op = _OPS.get(cmp.get("op"))
        if op is None:
            return False
        lhs = _operand_value(cmp.get("lhs"), ctx)
        rhs = _operand_value(cmp.get("rhs"), ctx)
        if lhs is None or rhs is None:
            return False
        return op(lhs, rhs)
    return False


# ── Intent emission ──

@dataclass
class StrategyIntent:
    strategy_id: str
    market_id: str
    condition_id: str
    token_id: str
    outcome: str
    side: str          # "buy" (open) or "sell" (close)
    target_qty: float
    limit_price: float
    reason: str        # "entry" | "exit" | "stop"
    est_fill_price: Optional[float] = None  # VWAP fill estimate (paper realism)
    crosses: bool = True                    # does the limit cross the book right now?


def _limit_price(book: dict, side: str, order_spec: dict) -> Optional[float]:
    """Marketable-limit price: cross the spread by limit_offset_bps for fill priority."""
    offset_bps = float(order_spec.get("limit_offset_bps", 0))
    if side == "buy":
        base = PolymarketService.get_best_ask_price(book)
        if base is None:
            return None
        return min(1.0, base * (1 + offset_bps / 10000.0))
    else:
        base = PolymarketService.get_best_bid_price(book)
        if base is None:
            return None
        return max(0.0, base * (1 - offset_bps / 10000.0))


def _target_qty(strategy: dict, price: float, budget: float) -> float:
    sizing = strategy.get("sizing", {})
    mode = sizing.get("mode", "fixed_usdc")
    value = float(sizing.get("value", 0))
    if mode == "pct_budget":
        notional = budget * value / 100.0
    else:  # fixed_usdc
        notional = value
    max_pos = float(sizing.get("max_position_usdc", notional or 0))
    notional = min(notional, max_pos) if max_pos else notional
    return (notional / price) if price > 0 else 0.0


def evaluate_strategy_for_market(
    strategy: dict, market: dict, ctx: StrategyContext, budget: float,
    open_position_count: int,
) -> Optional[StrategyIntent]:
    """
    Evaluate one strategy against one market and return at most one intent.
    Exit/stop (closing) take priority over entry (opening).
    """
    token_ref = strategy.get("token", strategy.get("universe", {}).get("token"))
    token_id = ctx.resolve_token_id(token_ref)
    if not token_id:
        return None
    book = ctx.order_books.get(token_id)
    if not book:
        return None
    outcome = next((t.get("outcome", "") for t in market.get("tokens", []) if t["token_id"] == token_id), "")
    pos = ctx.positions.get(token_id)
    order_spec = strategy.get("order", {})

    def _make(side, qty, price, reason):
        # Compute whether the limit crosses the book NOW and the VWAP fill estimate,
        # so paper execution can model fills realistically (a non-crossing limit rests
        # unfilled; a crossing one fills at VWAP, not at the optimistic limit price).
        notional = qty * price
        if side == "buy":
            best = PolymarketService.get_best_ask_price(book)
            crosses = best is not None and price >= best
            vwap = PolymarketService.get_fillable_price(book, "buy", notional)
        else:
            best = PolymarketService.get_best_bid_price(book)
            crosses = best is not None and price <= best
            vwap = PolymarketService.get_fillable_price(book, "sell", notional)
        return StrategyIntent(
            strategy["id"], market["market_id"], market.get("condition_id", ""),
            token_id, outcome, side, qty, price, reason,
            est_fill_price=vwap if vwap is not None else price, crosses=crosses,
        )

    # ── Closing side first: stop takes precedence over exit ──
    if pos and pos.qty > 0:
        if strategy.get("stop") and evaluate(strategy["stop"], ctx):
            price = _limit_price(book, "sell", order_spec)
            if price:
                return _make("sell", pos.qty, price, "stop")
        if strategy.get("exit") and evaluate(strategy["exit"], ctx):
            price = _limit_price(book, "sell", order_spec)
            if price:
                return _make("sell", pos.qty, price, "exit")
        return None  # holding; no entry while in a position

    # ── Opening side: respect max_open_positions ──
    max_open = int(strategy.get("max_open_positions", 1))
    if open_position_count >= max_open:
        return None
    if strategy.get("entry") and evaluate(strategy["entry"], ctx):
        price = _limit_price(book, "buy", order_spec)
        if not price:
            return None
        qty = _target_qty(strategy, price, budget)
        if qty <= 0:
            return None
        return _make("buy", qty, price, "entry")
    return None


def build_token_map(market: dict) -> dict:
    return {t.get("outcome", "").upper(): t["token_id"] for t in market.get("tokens", []) if t.get("token_id")}
