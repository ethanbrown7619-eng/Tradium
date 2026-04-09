"""
Tests for multi-outcome arbitrage detection logic.
"""
import pytest
from app.bot.strategies.multioutcome import calculate_multi_outcome_arbitrage


class TestMultiOutcomeArbitrage:
    def test_three_outcome_profitable(self):
        """Three outcomes summing well below $1.00."""
        result = calculate_multi_outcome_arbitrage(
            market_id="multi-1",
            condition_id="cond-1",
            market_question="Who wins?",
            market_slug="who-wins",
            outcome_prices={"A": 0.30, "B": 0.25, "C": 0.20},
            gas_cost_per_tx=0.005,
        )
        assert result.price_sum == pytest.approx(0.75)
        assert result.gross_profit == pytest.approx(0.25)
        # Worst case: cheapest outcome (C=0.20) wins -> fee = 0.02 * (1.0 - 0.20) = 0.016
        assert result.fee_worst_case == pytest.approx(0.016)
        # Gas = 0.005 * 3 = 0.015
        assert result.gas_cost == pytest.approx(0.015)
        # Net = 0.25 - 0.016 - 0.015 = 0.219
        assert result.net_profit == pytest.approx(0.219)
        assert result.is_profitable is True

    def test_no_arbitrage_sum_above_one(self):
        """Outcomes sum above $1.00."""
        result = calculate_multi_outcome_arbitrage(
            market_id="multi-2",
            condition_id="cond-2",
            market_question="Test",
            market_slug="test",
            outcome_prices={"A": 0.40, "B": 0.35, "C": 0.30},
        )
        assert result.price_sum == pytest.approx(1.05)
        assert result.is_profitable is False

    def test_marginal_eaten_by_fees(self):
        """Small margin disappears after fees."""
        result = calculate_multi_outcome_arbitrage(
            market_id="multi-3",
            condition_id="cond-3",
            market_question="Test",
            market_slug="test",
            outcome_prices={"A": 0.33, "B": 0.33, "C": 0.33},
            gas_cost_per_tx=0.005,
        )
        # Sum = 0.99, gross = 0.01
        # Fee = 0.02 * (1.0 - 0.33) = 0.0134
        # Gas = 0.015
        # Net = 0.01 - 0.0134 - 0.015 = -0.0184
        assert result.is_profitable is False

    def test_four_outcomes(self):
        """Four-way market."""
        result = calculate_multi_outcome_arbitrage(
            market_id="multi-4",
            condition_id="cond-4",
            market_question="Test",
            market_slug="test",
            outcome_prices={"A": 0.20, "B": 0.15, "C": 0.15, "D": 0.10},
            gas_cost_per_tx=0.005,
        )
        assert result.price_sum == pytest.approx(0.60)
        assert result.num_outcomes == 4
        # Worst case: D wins (cheapest), fee = 0.02 * (1.0 - 0.10) = 0.018
        assert result.fee_worst_case == pytest.approx(0.018)
        assert result.is_profitable is True

    def test_liquidity_tracking(self):
        result = calculate_multi_outcome_arbitrage(
            market_id="multi-5",
            condition_id="cond-5",
            market_question="Test",
            market_slug="test",
            outcome_prices={"A": 0.30, "B": 0.30, "C": 0.30},
            outcome_liquidities={"A": 1000.0, "B": 500.0, "C": 750.0},
        )
        assert result.min_liquidity == 500.0
