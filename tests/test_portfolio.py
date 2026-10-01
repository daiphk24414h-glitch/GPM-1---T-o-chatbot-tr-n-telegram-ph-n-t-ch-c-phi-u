from datetime import date

from app.database import UserStore
from app.models import Regime, Signal, TechnicalResult
from app.portfolio import PositionRiskEngine


def technical(price=110, signal=Signal.HOLD, regime=Regime.UP, atr=2):
    return TechnicalResult("FPT", date.today(), signal, regime, "TEST", 70, price,
                           93, 114, 1000, [], {"atr14": atr, "ema50": 100})


def test_position_store_roundtrip(tmp_path):
    store = UserStore(tmp_path / "bot.db", 1)
    store.upsert_position(1, 10, "fpt", 1000, 100)
    item = store.get_position(1, "FPT")
    assert item["quantity"] == 1000
    assert item["entry_price"] == 100
    store.update_position_risk(1, "FPT", 110, 104, 108, "PROTECT")
    assert store.get_position(1, "FPT")["active_stop"] == 104
    assert store.remove_position(1, "FPT")


def test_trailing_stop_activates_and_never_moves_down():
    engine = PositionRiskEngine()
    position = {"symbol": "FPT", "quantity": 1000, "entry_price": 100,
                "high_watermark": 100, "active_stop": 0}
    result = engine.assess(position, technical())
    assert result.trailing_active
    assert result.stop_price == 103.5
    later = {**position, "high_watermark": 110, "active_stop": result.stop_price}
    lower = engine.assess(later, technical(price=106, atr=3))
    assert lower.stop_price >= result.stop_price


def test_sell_in_profit_tightens_trailing_instead_of_immediate_fixed_target():
    engine = PositionRiskEngine()
    position = {"symbol": "FPT", "quantity": 1000, "entry_price": 100,
                "high_watermark": 112, "active_stop": 104}
    result = engine.assess(position, technical(price=110, signal=Signal.SELL, atr=2))
    assert result.trailing_active
    assert result.action == "PROTECT"
    assert result.stop_price == 107


def test_small_profit_does_not_activate_trailing_early():
    engine = PositionRiskEngine()
    position = {"symbol": "FPT", "quantity": 1000, "entry_price": 100,
                "high_watermark": 104, "active_stop": 0}
    result = engine.assess(position, technical(price=104, atr=2))
    assert not result.trailing_active
    assert result.stop_price == 96
