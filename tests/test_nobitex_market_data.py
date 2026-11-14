from app.nobitex_market_data import NobitexMarketData


def adapter():
    return NobitexMarketData("test-read-only-token")


def test_margin_market_list_keeps_both_directions_and_excludes_disabled():
    client = adapter()
    client._get = lambda path, params=None: {
        "status": "ok",
        "markets": {
            "BTCUSDT": {
                "srcCurrency": "btc", "dstCurrency": "usdt",
                "buyEnabled": True, "sellEnabled": True,
                "maxLeverage": "5", "positionFeeRate": "0.001",
            },
            "DOGEIRT": {
                "srcCurrency": "doge", "dstCurrency": "rls",
                "buyEnabled": False, "sellEnabled": True,
                "maxLeverage": "3", "positionFeeRate": "0.001",
            },
            "OFFUSDT": {
                "srcCurrency": "off", "dstCurrency": "usdt",
                "buyEnabled": False, "sellEnabled": False,
                "maxLeverage": "1", "positionFeeRate": "0",
            },
        },
    }
    markets = client.fetch_margin_markets()
    assert [item.symbol for item in markets] == ["BTCUSDT", "DOGEIRT"]
    assert markets[0].buy_enabled and markets[0].sell_enabled
    assert not markets[1].buy_enabled and markets[1].sell_enabled


def test_resolve_manual_symbol_prefers_usdt_for_base_and_accepts_full_pair():
    client = adapter()
    client.fetch_margin_markets = lambda force=False: [
        type("Market", (), {"symbol": "BTCIRT", "base": "BTC", "quote": "IRT"})(),
        type("Market", (), {"symbol": "BTCUSDT", "base": "BTC", "quote": "USDT"})(),
    ]
    assert client.resolve_manual_symbol("btc").symbol == "BTCUSDT"
    assert client.resolve_manual_symbol("BTC/IRT").symbol == "BTCIRT"


def test_fetch_ohlcv_converts_udf_seconds_to_ccxt_milliseconds():
    client = adapter()
    captured = {}

    def fake_get(path, params=None):
        captured.update(params or {})
        return {
            "s": "ok",
            "t": [1_700_000_000, 1_700_000_900],
            "o": ["10", "11"], "h": ["12", "13"],
            "l": ["9", "10"], "c": ["11", "12"], "v": ["5", "6"],
        }

    client._get = fake_get
    rows = client.fetch_ohlcv("BTCUSDT", "15m", 2)
    assert rows == [
        [1_700_000_000_000, 10.0, 12.0, 9.0, 11.0, 5.0],
        [1_700_000_900_000, 11.0, 13.0, 10.0, 12.0, 6.0],
    ]
    assert captured["resolution"] == "15"
    assert captured["countback"] == 2


def test_adapter_requires_token_for_margin_market_catalog():
    try:
        NobitexMarketData("")
    except ValueError as exc:
        assert "NOBITEX_API_TOKEN" in str(exc)
    else:
        raise AssertionError("read-only token should be required")
