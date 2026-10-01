from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd

from app.backtest import BacktestEngine
from app.charts import backtest_chart
from app.data import MarketDataService, normalize_ohlcv
from app.fundamental import FundamentalEngine, VnstockFreeFundamentalRepository
from app.models import Regime, Signal
from app.market import analyze_market
from app.technical import TechnicalEngine, market_regime
from verify_connections import extract_tier


def frame(rows=220, trend=0.4):
    dates = pd.date_range(date.today() - timedelta(days=rows * 2), periods=rows, freq="B")
    close = 100 + np.arange(rows) * trend + np.sin(np.arange(rows) / 6)
    return pd.DataFrame({"time": dates, "open": close - .2, "high": close + 1,
                         "low": close - 1, "close": close, "volume": np.full(rows, 2_000_000.0)})


def test_vnindex_vietnamese_columns_are_normalized():
    raw = pd.DataFrame({"Ngày": ["10/09/2026"], "Lần cuối": ["1,822.26"], "Mở": ["1,828.17"],
                        "Cao": ["1,830.73"], "Thấp": ["1,818.24"], "KL": ["97.98M"]})
    result = normalize_ohlcv(raw)
    assert result.iloc[0].close == 1822.26
    assert result.iloc[0].volume == 97_980_000


def test_vn100_universe_is_loaded_and_cached(tmp_path):
    service = MarketDataService(tmp_path / "missing.csv")
    with patch("vnstock.api.listing.Listing.symbols_by_group",
               return_value=pd.Series(["FPT", "VCB", "FPT", "VNINDEX", None])) as call:
        assert service.symbols_by_group("vn100") == ["FPT", "VCB"]
        assert service.symbols_by_group("VN100") == ["FPT", "VCB"]
        assert call.call_count == 1


def test_market_history_persists_across_service_restart(tmp_path):
    cache_dir = tmp_path / "cache"
    first = MarketDataService(tmp_path / "missing.csv", cache_dir=cache_dir)
    expected = frame(220)
    first._write_disk_cache("FPT", expected)
    second = MarketDataService(tmp_path / "missing.csv", cache_dir=cache_dir)
    actual = second.history("FPT", 220)
    assert len(actual) == 220
    assert actual.attrs["source"] == "Local persistent market cache"


def test_short_market_cache_cannot_impersonate_long_backtest_history(tmp_path):
    service = MarketDataService(tmp_path / "missing.csv", cache_dir=tmp_path / "cache")
    service._write_disk_cache("VIC", frame(260))
    assert service._read_disk_cache("VIC", 1500, fresh_only=True) is None


def test_short_refresh_does_not_truncate_deep_market_cache(tmp_path):
    service = MarketDataService(tmp_path / "missing.csv", cache_dir=tmp_path / "cache")
    service._write_disk_cache("VIC", frame(500))
    service._write_disk_cache("VIC", frame(260))
    cached = service._read_disk_cache("VIC", 500, fresh_only=True)
    assert cached is not None
    assert len(cached) == 500


def test_fundamental_snapshot_persists_across_service_restart(tmp_path):
    snapshot = {"schema": "vnstock_free_ratio", "as_of": date.today(),
                "company_type": "CT", "metrics": {"roe": .2}, "source": "test"}
    first = VnstockFreeFundamentalRepository(tmp_path, 12)
    first._write_disk("FPT", snapshot)
    second = VnstockFreeFundamentalRepository(tmp_path, 12)
    loaded = second._read_disk("FPT")
    assert loaded["as_of"] == date.today()
    assert loaded["metrics"]["roe"] == .2


def test_industry_query_resolves_without_treating_words_as_symbols(tmp_path):
    service = MarketDataService(tmp_path / "missing.csv")
    industries = pd.DataFrame([
        {"symbol": "MWG", "icb_name": "Bán lẻ", "icb_level": 3},
        {"symbol": "FRT", "icb_name": "Bán lẻ", "icb_level": 3},
        {"symbol": "VCB", "icb_name": "Ngân hàng", "icb_level": 3},
    ])
    with patch("vnstock.api.listing.Listing.symbols_by_industries", return_value=industries):
        name, symbols = service.symbols_by_industry("ban le")
    assert name == "Bán lẻ"
    assert symbols == ["MWG", "FRT"]


def test_missing_benchmark_blocks_signal():
    prices = frame()
    result = TechnicalEngine().analyze("FPT", prices, Regime.UNKNOWN)
    assert result.signal is Signal.BLOCKED


def test_technical_score_exposes_quant_factor_breakdown_and_position_cap():
    prices = frame(260, trend=.25)
    benchmark = frame(260, trend=.10)
    result = TechnicalEngine().analyze("FPT", prices, market_regime(benchmark), benchmark)
    assert {"relative_strength", "trend", "entry_quality", "volume", "risk_liquidity"} <= result.components.keys()
    assert result.components["relative_strength"] > 50
    assert result.recommended_shares * result.price <= 15_100_000


def test_fundamental_missing_data_is_not_scored():
    result = FundamentalEngine().analyze("FPT", {"revenue_cagr": .1})
    assert result.score is None
    assert result.status == "INCOMPLETE"


def test_market_regime_and_backtest_mark_to_market():
    prices = frame(260)
    regime = market_regime(prices)
    assert regime in {Regime.UP, Regime.SIDEWAY}
    result = BacktestEngine().run("FPT", prices, prices, fundamental_pass=True)
    assert result.final_equity > 0
    assert len(result.equity) == 60
    assert result.average_exposure_pct <= 15.5
    assert result.alpha_pct == result.total_return_pct - result.benchmark_return_pct
    if not result.trade_log.empty:
        assert {"return_pct", "holding_days"}.issubset(result.trade_log.columns)
        normal_exits = result.trade_log[result.trade_log.reason == "CONFIRMED_SIGNAL_EXIT"]
        assert (normal_exits.holding_days >= 3).all()


def test_high_priced_stock_position_is_not_rounded_down_to_zero():
    odd_lot = BacktestEngine(lot_size=1)
    board_lot = BacktestEngine(lot_size=100)
    args = (100_000_000, 100_000_000, 147_000, 8_285)
    assert odd_lot._position_quantity(*args) > 0
    assert board_lot._position_quantity(*args) == 0


def test_backtest_chart_renders_execution_points(tmp_path):
    prices = frame(260)
    result = BacktestEngine().run("FPT", prices, prices, fundamental_pass=True)
    output = backtest_chart(prices, result, "FPT", tmp_path / "backtest.png")
    assert output.exists()
    assert output.stat().st_size > 0


def test_backtest_honors_requested_evaluation_start():
    prices = frame(800)
    start = pd.Timestamp(prices.iloc[-1].time) - pd.DateOffset(years=2)
    result = BacktestEngine().run("FPT", prices, prices, evaluation_start=start)
    assert pd.Timestamp(result.equity.iloc[0].date) >= start.normalize()


def test_backtest_supports_three_month_window_with_prior_warmup():
    prices = frame(520)
    start = pd.Timestamp(prices.iloc[-1].time) - pd.DateOffset(months=3)
    result = BacktestEngine().run("FPT", prices, prices, evaluation_start=start)
    assert pd.Timestamp(result.equity.iloc[0].date) >= start.normalize()
    assert pd.Timestamp(result.equity.iloc[-1].date) == pd.Timestamp(prices.iloc[-1].time)


def test_market_outlook_uses_regime_weights():
    outlook = analyze_market(frame(260))
    assert outlook.horizon == "5–20 phiên"
    assert round(sum(outlook.weights.values()), 8) == 1
    if outlook.regime is Regime.SIDEWAY:
        assert outlook.weights["bollinger"] == .45
    assert 0 <= outlook.exposure_min <= outlook.exposure_max <= 100


def test_tier_parser_accepts_api_response_variants():
    assert extract_tier({"subscription": {"tier": "golden"}}) == "GOLDEN"
    assert extract_tier({"subscription": "community"}) == "COMMUNITY"
    assert extract_tier([{"plan": "silver"}]) == "SILVER"
    assert extract_tier({"data": {"membership": {"package": "bronze"}}}) == "BRONZE"
    assert extract_tier(None) == "UNKNOWN"
