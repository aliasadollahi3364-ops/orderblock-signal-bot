from app.market_data import MarketCandidate, _ticker_score


def test_ticker_score_rewards_tight_spread_without_changing_volume_basis():
    wide_spread, wide_score = _ticker_score(1_000_000, 99.0, 101.0)
    tight_spread, tight_score = _ticker_score(1_000_000, 99.9, 100.1)
    assert tight_spread < wide_spread
    assert tight_score > wide_score


def test_market_candidate_retains_ticker_last_price():
    candidate = MarketCandidate("BTC/USDT:USDT", 1000, 0.05, 1050, 65000)
    assert candidate.last_price == 65000

def test_market_candidate_defaults_tick_size_for_legacy_callers():
    candidate = MarketCandidate("BTC/USDT:USDT", 1000, 0.05, 1050, 65000)
    assert candidate.tick_size == 0.00000001


def test_weex_tick_size_uses_exchange_price_place_and_end_step():
    from app.market_data import WeexMarketData

    instance = WeexMarketData.__new__(WeexMarketData)
    assert instance._market_tick_size(
        {"info": {"pricePlace": "4", "priceEndStep": "5"}}, 1.25
    ) == pytest.approx(0.0005)

def _market_data_for_manual_symbol(monkeypatch):
    from app.market_data import WeexMarketData

    instance = WeexMarketData.__new__(WeexMarketData)
    instance.quote_currency = "USDT"
    instance._markets = {
        "SPCX/USDT:USDT": {
            "symbol": "SPCX/USDT:USDT",
            "base": "SPCX",
            "quote": "USDT",
            "settle": "USDT",
            "active": True,
            "swap": True,
            "linear": True,
        }
    }
    monkeypatch.setattr(instance, "_fetch_ticker_cached", lambda symbol: {
        "last": 1.25, "quoteVolume": 50000
    })
    return instance


import pytest


@pytest.mark.parametrize("requested", ["SPCX", "SPCXUSDT", "SPCX/USDT", "SPCX/USDT:USDT"])
def test_manual_symbol_resolves_ccxt_settled_swap_symbol(monkeypatch, requested):
    from app.market_data import MarketCandidate

    instance = _market_data_for_manual_symbol(monkeypatch)
    candidate = instance.resolve_manual_symbol(requested)
    assert isinstance(candidate, MarketCandidate)
    assert candidate.symbol == "SPCX/USDT:USDT"
    assert candidate.last_price == 1.25


def test_manual_symbol_rejects_inactive_or_non_swap_market(monkeypatch):
    instance = _market_data_for_manual_symbol(monkeypatch)
    instance._markets["SPCX/USDT:USDT"]["active"] = False
    with pytest.raises(ValueError, match="قرارداد دائمی"):
        instance.resolve_manual_symbol("SPCX")


def test_weex_public_trade_and_orderbook_data_are_normalized_and_cached(monkeypatch):
    from app.market_data import WeexMarketData

    class FakeExchange:
        def __init__(self):
            self.trade_calls = 0
            self.book_calls = 0

        def load_markets(self):
            return {}

        def fetch_trades(self, symbol, limit):
            self.trade_calls += 1
            return [
                {"side": "buy", "amount": 2, "price": 10},
                {"side": "sell", "amount": 1, "price": 11},
                {"side": None, "amount": 4, "price": 12},
            ]

        def fetch_order_book(self, symbol, limit):
            self.book_calls += 1
            return {"bids": [[10, 5], [9, 2]], "asks": [[11, 3]]}

    instance = WeexMarketData.__new__(WeexMarketData)
    instance._markets = {}
    instance._trades_cache = {}
    instance._orderbook_cache = {}
    instance.exchange = FakeExchange()

    trades = instance.fetch_trades("BTC/USDT:USDT")
    assert trades == [
        {"type": "buy", "volume": 2.0, "price": 10.0},
        {"type": "sell", "volume": 1.0, "price": 11.0},
    ]
    assert instance.fetch_trades("BTC/USDT:USDT") == trades
    assert instance.exchange.trade_calls == 1

    book = instance.fetch_orderbook("BTC/USDT:USDT")
    assert book == {"bids": [[10, 5], [9, 2]], "asks": [[11, 3]]}
    assert instance.fetch_orderbook("BTC/USDT:USDT") == book
    assert instance.exchange.book_calls == 1


def test_weex_adapter_exposes_no_exchange_wide_market_discovery():
    from app.market_data import WeexMarketData

    assert not hasattr(WeexMarketData, "fetch_usdt_perpetuals")
