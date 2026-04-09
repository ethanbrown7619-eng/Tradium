"""Tests for the Polymarket service utility methods."""
import pytest
from app.services.polymarket import PolymarketService


class TestOrderBookUtils:
    def test_calculate_order_book_depth(self):
        book = {
            "bids": [
                {"price": "0.50", "size": "100"},
                {"price": "0.49", "size": "200"},
            ],
            "asks": [
                {"price": "0.51", "size": "150"},
                {"price": "0.52", "size": "100"},
            ],
        }
        bid_depth = PolymarketService.calculate_order_book_depth(book, "bids")
        # 0.50*100 + 0.49*200 = 50 + 98 = 148
        assert bid_depth == pytest.approx(148.0)

        ask_depth = PolymarketService.calculate_order_book_depth(book, "asks")
        # 0.51*150 + 0.52*100 = 76.5 + 52 = 128.5
        assert ask_depth == pytest.approx(128.5)

    def test_calculate_empty_book(self):
        assert PolymarketService.calculate_order_book_depth({}, "bids") == 0.0
        assert PolymarketService.calculate_order_book_depth(None, "bids") == 0.0

    def test_get_best_ask_price(self):
        book = {
            "asks": [
                {"price": "0.55"},
                {"price": "0.51"},
                {"price": "0.53"},
            ]
        }
        assert PolymarketService.get_best_ask_price(book) == pytest.approx(0.51)

    def test_get_best_ask_empty(self):
        assert PolymarketService.get_best_ask_price({}) is None
        assert PolymarketService.get_best_ask_price(None) is None
        assert PolymarketService.get_best_ask_price({"asks": []}) is None

    def test_get_fillable_price_enough_liquidity(self):
        book = {
            "asks": [
                {"price": "0.50", "size": "100"},
                {"price": "0.51", "size": "200"},
                {"price": "0.52", "size": "300"},
            ]
        }
        # Want to buy $50 worth: fills entirely at 0.50 level
        # $50 / 0.50 = 100 tokens, cost = $50
        price = PolymarketService.get_fillable_price(book, "buy", 50.0)
        assert price == pytest.approx(0.50)

        # Want to buy $60 worth: fills $50 at 0.50 then $10 at 0.51
        # $50 -> 100 tokens, $10 -> 19.6078 tokens
        # Total tokens = 119.6078, total cost = $60
        # Avg price = 60 / 119.6078 ≈ 0.50164
        price = PolymarketService.get_fillable_price(book, "buy", 60.0)
        assert price is not None
        assert price > 0.50
        assert price < 0.51

    def test_get_fillable_price_not_enough_liquidity(self):
        book = {
            "asks": [
                {"price": "0.50", "size": "10"},
            ]
        }
        # Only $5 of liquidity, trying to buy $100
        price = PolymarketService.get_fillable_price(book, "buy", 100.0)
        assert price is None
