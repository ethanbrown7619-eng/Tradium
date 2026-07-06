"""
Pydantic models for user-defined strategies.

The critical job here is SAVE-TIME VALIDATION: a strategy is only accepted if
every indicator it references is in the engine's whitelist and every operator is
in the closed set. This is the gate that guarantees the stored strategy can only
ever be evaluated by the interpreter — there is no path to arbitrary code.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, Any
from uuid import UUID
from datetime import datetime

from app.bot.strategies.engine import INDICATOR_NAMES, OPERATORS


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


def _validate_condition(node: Any, path: str = "root") -> None:
    """
    Recursively validate a condition AST. Raises ValueError on any unknown
    indicator, unknown operator, or malformed node. This is what makes an
    unknown indicator a hard save-time rejection rather than a silent no-op.
    """
    if not isinstance(node, dict):
        raise ValueError(f"{path}: condition must be an object")
    keys = set(node.keys())
    if keys == {"all"} or keys == {"any"}:
        key = "all" if "all" in node else "any"
        children = node[key]
        if not isinstance(children, list) or not children:
            raise ValueError(f"{path}.{key}: must be a non-empty list")
        for i, c in enumerate(children):
            _validate_condition(c, f"{path}.{key}[{i}]")
    elif keys == {"not"}:
        _validate_condition(node["not"], f"{path}.not")
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
    if isinstance(operand, (int, float)):
        return
    if isinstance(operand, dict) and "name" in operand:
        name = operand["name"]
        if name not in INDICATOR_NAMES:
            raise ValueError(
                f"{path}: unknown indicator {name!r}; allowed: {sorted(INDICATOR_NAMES)}"
            )
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
