"""
Pydantic models for user-defined strategies.

The critical job here is SAVE-TIME VALIDATION: a strategy is only accepted if
every indicator it references is in the engine's whitelist and every operator is
in the closed set. This is the gate that guarantees the stored strategy can only
ever be evaluated by the interpreter — there is no path to arbitrary code.
"""
from __future__ import annotations

import math
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, Any
from uuid import UUID
from datetime import datetime

from app.bot.strategies.engine import INDICATOR_NAMES, OPERATORS

# Bounds that stop a saved strategy from degrading the worker (denial-of-compute).
# These are not about code execution — they cap how much work one strategy can
# force every scan tick, and how big a payload the validator will accept.
MAX_CONDITION_DEPTH = 20
MAX_CONDITION_NODES = 200
MAX_WINDOW = 500          # sma / price_change_pct lookback
MAX_VWAP_SIZE = 1_000_000.0  # vwap_at probe size (USDC)


class Sizing(BaseModel):
    mode: str = Field(default="fixed_usdc")  # fixed_usdc | pct_budget
    value: float = Field(default=10.0, ge=0)
    max_position_usdc: float = Field(default=100.0, ge=0)

    @field_validator("mode")
    @classmethod
    def _mode_ok(cls, v):
        if v not in ("fixed_usdc", "pct_budget"):
            raise ValueError("sizing.mode must be 'fixed_usdc' or 'pct_budget'")
        return v


class OrderSpec(BaseModel):
    type: str = Field(default="marketable_limit")  # limit | marketable_limit
    limit_offset_bps: float = Field(default=50.0, ge=0, le=5000)
    tif: str = Field(default="GTC")  # GTC | FOK | IOC

    @field_validator("type")
    @classmethod
    def _type_ok(cls, v):
        if v not in ("limit", "marketable_limit"):
            raise ValueError("order.type must be 'limit' or 'marketable_limit'")
        return v


class Universe(BaseModel):
    market_ids: list[str] = Field(default_factory=list)
    slug_contains: Optional[str] = None
    token: Optional[str] = None  # which outcome/token this strategy trades


def _validate_condition(node: Any, path: str = "root", depth: int = 0, counter: Optional[list] = None) -> None:
    """
    Recursively validate a condition AST. Raises ValueError on any unknown
    indicator, unknown operator, malformed node, excessive nesting, or too many
    nodes. This makes an unknown indicator a hard save-time rejection AND caps how
    much work one strategy can force per tick.
    """
    if counter is None:
        counter = [0]
    counter[0] += 1
    if depth > MAX_CONDITION_DEPTH:
        raise ValueError(f"{path}: condition nesting too deep (max {MAX_CONDITION_DEPTH})")
    if counter[0] > MAX_CONDITION_NODES:
        raise ValueError(f"condition has too many nodes (max {MAX_CONDITION_NODES})")

    if not isinstance(node, dict):
        raise ValueError(f"{path}: condition must be an object")
    keys = set(node.keys())
    if keys == {"all"} or keys == {"any"}:
        key = "all" if "all" in node else "any"
        children = node[key]
        if not isinstance(children, list) or not children:
            raise ValueError(f"{path}.{key}: must be a non-empty list")
        for i, c in enumerate(children):
            _validate_condition(c, f"{path}.{key}[{i}]", depth + 1, counter)
    elif keys == {"not"}:
        _validate_condition(node["not"], f"{path}.not", depth + 1, counter)
    elif keys == {"cmp"}:
        cmp = node["cmp"]
        if not isinstance(cmp, dict):
            raise ValueError(f"{path}.cmp: must be an object")
        if cmp.get("op") not in OPERATORS:
            raise ValueError(f"{path}.cmp.op: unknown operator {cmp.get('op')!r}; allowed: {sorted(OPERATORS)}")
        _validate_operand(cmp.get("lhs"), f"{path}.cmp.lhs")
        _validate_operand(cmp.get("rhs"), f"{path}.cmp.rhs")
    else:
        raise ValueError(f"{path}: node must have exactly one of 'all','any','not','cmp' (got {sorted(keys)})")


def _validate_operand(operand: Any, path: str) -> None:
    # bool is an int subclass — reject it explicitly so True/False can't sneak in as numbers
    if isinstance(operand, bool):
        raise ValueError(f"{path}: operand must be a number or indicator, not a boolean")
    if isinstance(operand, (int, float)):
        if not math.isfinite(operand):
            raise ValueError(f"{path}: number must be finite (no NaN/Inf)")
        return
    if isinstance(operand, dict) and "name" in operand:
        name = operand["name"]
        if name not in INDICATOR_NAMES:
            raise ValueError(
                f"{path}: unknown indicator {name!r}; allowed: {sorted(INDICATOR_NAMES)}"
            )
        # Bound numeric indicator params so a strategy can't force huge per-tick work
        if "window" in operand:
            w = operand["window"]
            if isinstance(w, bool) or not isinstance(w, int) or w < 1 or w > MAX_WINDOW:
                raise ValueError(f"{path}: window must be an int in [1, {MAX_WINDOW}]")
        if "size" in operand:
            s = operand["size"]
            if isinstance(s, bool) or not isinstance(s, (int, float)) or not math.isfinite(s) \
                    or s <= 0 or s > MAX_VWAP_SIZE:
                raise ValueError(f"{path}: size must be a number in (0, {MAX_VWAP_SIZE}]")
        return
    raise ValueError(f"{path}: operand must be a number or an indicator object with a 'name'")


class StrategyDefinition(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    enabled: bool = False
    token: Optional[str] = None                 # convenience: outcome/token traded
    universe: Universe = Field(default_factory=Universe)
    entry: Optional[dict] = None
    exit: Optional[dict] = None
    stop: Optional[dict] = None
    sizing: Sizing = Field(default_factory=Sizing)
    order: OrderSpec = Field(default_factory=OrderSpec)
    cooldown_seconds: int = Field(default=60, ge=0)
    max_open_positions: int = Field(default=1, ge=1, le=100)

    @model_validator(mode="after")
    def _validate_conditions(self):
        if self.entry is not None:
            _validate_condition(self.entry, "entry")
        if self.exit is not None:
            _validate_condition(self.exit, "exit")
        if self.stop is not None:
            _validate_condition(self.stop, "stop")
        if self.entry is None and self.exit is None and self.stop is None:
            raise ValueError("strategy must define at least one of entry/exit/stop")
        return self


class StrategyResponse(BaseModel):
    id: UUID
    user_id: UUID
    name: str
    enabled: bool
    definition: dict
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
