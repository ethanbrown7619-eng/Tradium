"""
Tests for binary arbitrage detection logic.
Critical: verifies the 2% fee on winnings is calculated correctly.
"""
import pytest
from app.bot.strategies.binary import calculate_binary_arbitrage, BinaryArbitrageResult


class TestBinaryArbitrageCalculation:
    """Test the core arbitrage profit calculation with fee handling."""

    def test_clear_arbitrage_opportunity(self):
        """YES=0.45 + NO=0.52 = 0.97, should be profitable after fees."""
        result = calculate_binary_arbitrage(
            market_id="test-1",
            condition_id="cond-1",
            market_question="Test market",
            market_slug="test-market",
            yes_ask=0.45,
            no_ask=0.52,
            gas_cost=0.01,
        )
        assert result.price_sum == pytest.approx(0.97)
        assert result.gross_profit == pytest.approx(0.03)
        # Worst case fee: max(0.02*(1-0.45), 0.02*(1-0.52)) = max(0.011, 0.0096) = 0.011
        assert result.fee_worst_case == pytest.approx(0.011)
        # Net = 0.03 - 0.011 - 0.01 = 0.009
        assert result.net_profit == pytest.approx(0.009)
        assert result.is_profitable is True

    def test_no_arbitrage_when_sum_above_one(self):
        """YES=0.55 + NO=0.50 = 1.05, no opportunity."""
        result = calculate_binary_arbitrage(
            market_id="test-2",
            condition_id="cond-2",
            market_question="Test",
            market_slug="test",
            yes_ask=0.55,
            no_ask=0.50,
        )
        assert result.price_sum == pytest.approx(1.05)
        assert result.gross_profit == pytest.approx(-0.05)
        assert result.is_profitable is False

    def test_marginal_opportunity_eaten_by_fees(self):
        """Small spread that disappears after fees + gas."""
        result = calculate_binary_arbitrage(
            market_id="test-3",
            condition_id="cond-3",
            market_question="Test",
            market_slug="test",
            yes_ask=0.49,
            no_ask=0.50,
            gas_cost=0.01,
        )
        # Sum = 0.99, gross = 0.01
        # Fee worst case: max(0.02*(1-0.49), 0.02*(1-0.50)) = max(0.0102, 0.01) = 0.0102
        # Net = 0.01 - 0.0102 - 0.01 = -0.0102
        assert result.gross_profit == pytest.approx(0.01)
        assert result.is_profitable is False

    def test_fee_uses_worst_case_side(self):
        """
        Verify that we use the WORST CASE fee (cheapest token wins = highest winnings).
        YES=0.20, NO=0.70: if YES wins, winnings=0.80, fee=0.016
        if NO wins, winnings=0.30, fee=0.006
        Worst case = YES wins = 0.016
        """
        result = calculate_binary_arbitrage(
            market_id="test-4",
            condition_id="cond-4",
            market_question="Test",
            market_slug="test",
            yes_ask=0.20,
            no_ask=0.70,
            gas_cost=0.01,
        )
        assert result.price_sum == pytest.approx(0.90)
        assert result.gross_profit == pytest.approx(0.10)
        # Worst case: YES side wins -> fee = 0.02 * (1.0 - 0.20) = 0.016
        assert result.fee_worst_case == pytest.approx(0.016)
        # Net = 0.10 - 0.016 - 0.01 = 0.074
        assert result.net_profit == pytest.approx(0.074)
        assert result.is_profitable is True

    def test_exact_dollar_sum(self):
        """YES=0.50 + NO=0.50 = 1.00, no profit."""
        result = calculate_binary_arbitrage(
            market_id="test-5",
            condition_id="cond-5",
            market_question="Test",
            market_slug="test",
            yes_ask=0.50,
            no_ask=0.50,
        )
        assert result.price_sum == pytest.approx(1.0)
        assert result.gross_profit == pytest.approx(0.0)
        assert result.is_profitable is False

    def test_extreme_skew(self):
        """YES=0.01, NO=0.01 = 0.02, massive opportunity."""
        result = calculate_binary_arbitrage(
            market_id="test-6",
            condition_id="cond-6",
            market_question="Test",
            market_slug="test",
            yes_ask=0.01,
            no_ask=0.01,
            gas_cost=0.01,
        )
        assert result.price_sum == pytest.approx(0.02)
        assert result.gross_profit == pytest.approx(0.98)
        # Fee: max(0.02*0.99, 0.02*0.99) = 0.0198
        assert result.fee_worst_case == pytest.approx(0.0198)
        assert result.is_profitable is True
        assert result.net_profit_pct > 100  # Huge return

    def test_net_profit_pct_calculation(self):
        """Verify percentage is profit relative to cost."""
        result = calculate_binary_arbitrage(
            market_id="test-7",
            condition_id="cond-7",
            market_question="Test",
            market_slug="test",
            yes_ask=0.45,
            no_ask=0.50,
            gas_cost=0.01,
        )
        # Sum = 0.95, pct = net_profit / 0.95 * 100
        expected_pct = result.net_profit / 0.95 * 100
        assert result.net_profit_pct == pytest.approx(expected_pct)

    def test_liquidity_tracking(self):
        """Verify min_liquidity is tracked correctly."""
        result = calculate_binary_arbitrage(
            market_id="test-8",
            condition_id="cond-8",
            market_question="Test",
            market_slug="test",
            yes_ask=0.40,
            no_ask=0.50,
            yes_liquidity=1000.0,
            no_liquidity=500.0,
        )
        assert result.yes_liquidity == 1000.0
        assert result.no_liquidity == 500.0
        assert result.min_liquidity == 500.0
