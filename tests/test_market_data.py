from app.market_data import MarketCandidate, _ticker_score


def test_ticker_score_rewards_tight_spread_without_changing_volume_basis():
    wide_spread, wide_score = _ticker_score(1_000_000, 99.0, 101.0)
    tight_spread, tight_score = _ticker_score(1_000_000, 99.9, 100.1)
    assert tight_spread < wide_spread
    assert tight_score > wide_score


def test_market_candidate_retains_ticker_last_price():
    candidate = MarketCandidate("BTC/USDT:USDT", 1000, 0.05, 1050, 65000)
    assert candidate.last_price == 65000
