"""
Phase 1 tests: market normalization + VWAP-at-size detection.

Proves the scanner can parse BOTH the CLOB and Gamma API shapes into a stable
internal market (with real token IDs), and that detection prices at fillable
size rather than a top-of-book quote that won't fill.
"""
import pytest

from app.services.polymarket import PolymarketService, _parse_json_list
from app.bot.strategies.binary import scan_binary_markets


# ── Recorded-shape fixtures (trimmed to the fields the normalizer reads) ──

CLOB_MARKET = {
    "condition_id": "0xcond123",
    "question": "Will it rain tomorrow?",
    "market_slug": "will-it-rain",
    "active": True,
    "closed": False,
    "minimum_tick_size": "0.01",
    "minimum_order_size": "5",
    "category": "weather",
    "tokens": [
        {"token_id": "1001", "outcome": "Yes", "price": 0.45},
        {"token_id": "1002", "outcome": "No", "price": 0.52},
    ],
}

GAMMA_MARKET = {
    "id": "77",
    "conditionId": "0xcondABC",
    "slug": "election-2028",
    "question": "Who wins in 2028?",
    "active": True,
    "closed": False,
    # Gamma returns these as JSON-ENCODED STRINGS, not arrays
    "clobTokenIds": "[\"2001\", \"2002\"]",
    "outcomes": "[\"Yes\", \"No\"]",
    "volumeNum": 12345.6,
}


class TestParseJsonList:
    def test_parses_encoded_string(self):
        assert _parse_json_list('["a", "b"]') == ["a", "b"]

    def test_passthrough_list(self):
        assert _parse_json_list(["a", "b"]) == ["a", "b"]

    def test_garbage_returns_empty(self):
        assert _parse_json_list("not json") == []
        assert _parse_json_list(None) == []


class TestNormalizeMarket:
    def test_clob_shape(self):
        norm = PolymarketService.normalize_market(CLOB_MARKET)
        assert norm is not None
        assert norm["condition_id"] == "0xcond123"
        assert len(norm["tokens"]) == 2
        assert norm["tokens"][0]["token_id"] == "1001"
        assert norm["tokens"][0]["outcome"] == "Yes"
        assert norm["min_tick_size"] == 0.01
        assert norm["min_order_size"] == 5.0
        assert norm["active"] and not norm["closed"]

    def test_gamma_shape_decodes_stringified_tokens(self):
        norm = PolymarketService.normalize_market(GAMMA_MARKET)
        assert norm is not None
        assert norm["condition_id"] == "0xcondABC"
        # The stringified clobTokenIds must decode into real token IDs
        assert [t["token_id"] for t in norm["tokens"]] == ["2001", "2002"]
        assert [t["outcome"] for t in norm["tokens"]] == ["Yes", "No"]
        assert norm["volume"] == pytest.approx(12345.6)

    def test_both_shapes_yield_same_stable_keys(self):
        a = PolymarketService.normalize_market(CLOB_MARKET)
        b = PolymarketService.normalize_market(GAMMA_MARKET)
        assert set(a.keys()) == set(b.keys())

    def test_untradeable_record_returns_none(self):
        assert PolymarketService.normalize_market({"foo": "bar"}) is None
        assert PolymarketService.normalize_market({"clobTokenIds": "[]"}) is None


# ── VWAP-at-size detection ──

def _book(asks):
    return {"bids": [], "asks": [{"price": str(p), "size": str(s)} for p, s in asks]}


class TestFillablePriceDetection:
    def test_thin_top_of_book_that_wont_fill_is_rejected(self):
        """
        Best ask looks like a fat arb (0.45 + 0.45 = 0.90), but only $10 sits
        there; filling $500 walks into 0.60 asks so the real sum >= 1.0.
        Detection must NOT report an opportunity.
        """
        market = {
            "id": "m", "condition_id": "c", "question": "q", "slug": "q",
            "tokens": [
                {"token_id": "yes", "outcome": "YES"},
                {"token_id": "no", "outcome": "NO"},
            ],
        }
        # $10 at 0.45, then a wall at 0.60. VWAP over $500 ~ 0.60.
        thin = _book([(0.45, 22), (0.60, 5000)])  # 0.45*22 = $9.9 then 0.60
        order_books = {"yes": thin, "no": thin}

        results = scan_binary_markets([market], order_books, min_liquidity=500.0)
        assert results == []

    def test_deep_cheap_book_yields_opportunity(self):
        """Deep liquidity at low prices: a real, fillable arb."""
        market = {
            "id": "m", "condition_id": "c", "question": "q", "slug": "q",
            "tokens": [
                {"token_id": "yes", "outcome": "YES"},
                {"token_id": "no", "outcome": "NO"},
            ],
        }
        # Plenty of depth at 0.45 / 0.50 -> sum 0.95, fillable for $500 each
        yes = _book([(0.45, 5000)])
        no = _book([(0.50, 5000)])
        order_books = {"yes": yes, "no": no}

        results = scan_binary_markets([market], order_books, min_liquidity=500.0)
        assert len(results) == 1
        assert results[0].is_profitable
        # Liquidity recorded is the size we verified fillable
        assert results[0].min_liquidity == pytest.approx(500.0)
