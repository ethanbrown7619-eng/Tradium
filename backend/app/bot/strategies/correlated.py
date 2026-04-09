"""
Correlated Market Detection Strategy (Strategy 3)

Detects logically related markets that are mispriced relative to each other.
Flags only — does NOT auto-execute. Presented for manual review.

Examples of logical inconsistencies:
- "X wins election" > "X wins their state" (impossible: must win state to win)
- "Event in 2024" < "Event in Q3 2024" (subset can't exceed superset)
- Parent event probability < child event probability
"""
from dataclasses import dataclass
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class CorrelatedOpportunity:
    market_a_id: str
    market_a_question: str
    market_a_price: float
    market_b_id: str
    market_b_question: str
    market_b_price: float
    relationship: str  # "subset", "superset", "complement", "related"
    inconsistency: str  # Human-readable description
    confidence_score: float  # 0.0 to 1.0
    estimated_profit_pct: float


def detect_correlated_opportunities(markets: list[dict]) -> list[CorrelatedOpportunity]:
    """
    Scan markets for logically correlated pricing inconsistencies.
    This uses keyword and pattern matching to identify related markets.
    """
    opportunities = []

    # Build lookup by keywords for faster matching
    market_list = []
    for m in markets:
        question = m.get("question", "")
        tokens = m.get("tokens", [])
        yes_price = None
        for t in tokens:
            if t.get("outcome", "").upper() == "YES":
                yes_price = float(t.get("price", 0))
                break
        if yes_price is not None and yes_price > 0:
            market_list.append({
                "id": m.get("id", ""),
                "question": question,
                "yes_price": yes_price,
                "slug": m.get("slug", ""),
                "tags": m.get("tags", []),
            })

    # Check for time-based subset relationships
    # e.g., "X by 2024" vs "X by Q3 2024"
    for i, a in enumerate(market_list):
        for j, b in enumerate(market_list):
            if i >= j:
                continue

            opp = _check_time_subset(a, b)
            if opp:
                opportunities.append(opp)
                continue

            opp = _check_conditional_subset(a, b)
            if opp:
                opportunities.append(opp)

    return opportunities


def _check_time_subset(a: dict, b: dict) -> Optional[CorrelatedOpportunity]:
    """Check if one market is a time-based subset of another."""
    q_a = a["question"].lower()
    q_b = b["question"].lower()

    # Look for year references
    year_pattern = r'\b(20\d{2})\b'
    quarter_pattern = r'\b(q[1-4])\s*(20\d{2})\b'

    a_quarters = re.findall(quarter_pattern, q_a)
    b_quarters = re.findall(quarter_pattern, q_b)
    a_years = re.findall(year_pattern, q_a)
    b_years = re.findall(year_pattern, q_b)

    # If A has a specific quarter and B has just the year
    if a_quarters and b_years and not b_quarters:
        q, yr = a_quarters[0]
        if yr in b_years:
            # A is a subset of B (Q3 2024 is subset of 2024)
            # So price of A should be <= price of B
            if a["yes_price"] > b["yes_price"] + 0.02:  # 2% threshold
                price_diff = a["yes_price"] - b["yes_price"]
                return CorrelatedOpportunity(
                    market_a_id=a["id"],
                    market_a_question=a["question"],
                    market_a_price=a["yes_price"],
                    market_b_id=b["id"],
                    market_b_question=b["question"],
                    market_b_price=b["yes_price"],
                    relationship="subset",
                    inconsistency=f"'{a['question']}' ({a['yes_price']:.2f}) is a time subset of '{b['question']}' ({b['yes_price']:.2f}) but priced higher",
                    confidence_score=min(0.9, 0.5 + price_diff),
                    estimated_profit_pct=price_diff / a["yes_price"] * 100 if a["yes_price"] > 0 else 0,
                )

    return None


def _check_conditional_subset(a: dict, b: dict) -> Optional[CorrelatedOpportunity]:
    """Check for conditional/logical subset relationships."""
    q_a = a["question"].lower()
    q_b = b["question"].lower()

    # Look for "X wins election" vs "X wins [state]"
    # The person who wins the election must also win certain states
    win_pattern = r'(\w+(?:\s+\w+)?)\s+(?:wins?|elected|become)\s+(.+)'
    a_match = re.search(win_pattern, q_a)
    b_match = re.search(win_pattern, q_b)

    if not a_match or not b_match:
        return None

    a_subject = a_match.group(1).strip()
    b_subject = b_match.group(1).strip()
    a_target = a_match.group(2).strip()
    b_target = b_match.group(2).strip()

    # Same subject, different targets
    if a_subject == b_subject and a_target != b_target:
        # Check for general vs specific (e.g., "presidency" vs "primary")
        general_terms = ["president", "election", "presidency", "nomination"]
        specific_terms = ["state", "primary", "caucus", "district"]

        a_is_general = any(term in a_target for term in general_terms)
        b_is_specific = any(term in b_target for term in specific_terms)

        if a_is_general and b_is_specific:
            # General outcome should be priced <= specific required step
            # Actually: winning the election implies winning certain states,
            # so election price should be <= state price (for necessary states)
            # This is complex — flag with lower confidence
            if a["yes_price"] > b["yes_price"] + 0.05:
                price_diff = a["yes_price"] - b["yes_price"]
                return CorrelatedOpportunity(
                    market_a_id=a["id"],
                    market_a_question=a["question"],
                    market_a_price=a["yes_price"],
                    market_b_id=b["id"],
                    market_b_question=b["question"],
                    market_b_price=b["yes_price"],
                    relationship="related",
                    inconsistency=f"'{a['question']}' ({a['yes_price']:.2f}) seems mispriced relative to '{b['question']}' ({b['yes_price']:.2f})",
                    confidence_score=0.4,
                    estimated_profit_pct=price_diff / a["yes_price"] * 100 if a["yes_price"] > 0 else 0,
                )

    return None
