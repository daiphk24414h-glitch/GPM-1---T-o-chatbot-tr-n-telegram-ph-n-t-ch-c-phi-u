from __future__ import annotations

from dataclasses import dataclass

from .models import Regime, Signal, TechnicalResult


@dataclass(slots=True)
class PositionAssessment:
    symbol: str
    price: float
    entry_price: float
    quantity: int
    pnl_pct: float
    high_watermark: float
    stop_price: float
    risk_multiple: float
    trailing_active: bool
    action: str
    reason: str
    regime: Regime
    technical_signal: Signal


class PositionRiskEngine:
    """Long-only position management with an ATR stop that can only ratchet upward."""

    def assess(self, position: dict, technical: TechnicalResult,
               live_price: float | None = None, live_high: float | None = None) -> PositionAssessment:
        entry = float(position["entry_price"])
        quantity = int(position["quantity"])
        price = float(live_price or technical.price)
        atr = max(float(technical.indicators.get("atr14") or 0), price * .01)
        peak = max(float(position.get("high_watermark") or entry), float(live_high or price), price)

        # Emergency loss is capped near 7%, while volatile shares may receive
        # a tighter 2-ATR stop. A gap can still fill below this level.
        initial_stop = max(entry * .93, entry - 2 * atr)
        previous_stop = float(position.get("active_stop") or 0)
        risk_distance = max(entry - initial_stop, entry * .01)
        risk_multiple = (price - entry) / risk_distance
        pnl_pct = (price / entry - 1) * 100
        # A small +3% bounce is common noise in Vietnam. Protect the trend only
        # after both +1R and +5% have been achieved.
        trailing_active = ((pnl_pct >= 5 and risk_multiple >= 1)
                           or previous_stop > initial_stop)

        stop = max(previous_stop, initial_stop)
        if trailing_active:
            multiplier = {Regime.UP: 3.25, Regime.SIDEWAY: 2.0,
                          Regime.DOWN: 1.5, Regime.UNKNOWN: 2.0}[technical.regime]
            if technical.signal is Signal.SELL:
                multiplier = min(multiplier, 2.5 if technical.regime is Regime.UP else 1.8)
            stop = max(stop, peak - multiplier * atr)
            if risk_multiple >= 1:
                stop = max(stop, entry * 1.0035)  # fees, tax and modest slippage buffer
        ema50 = technical.indicators.get("ema50")
        breakdown = (technical.regime is Regime.DOWN and isinstance(ema50, (int, float))
                     and price < float(ema50))
        if price <= stop:
            action = "EXIT"
            reason = "Giá đã chạm ngưỡng bảo vệ vị thế."
        elif breakdown:
            action = "EXIT"
            reason = "Giá nằm dưới EMA50 khi thị trường ở trạng thái DOWN."
        elif technical.signal is Signal.SELL and trailing_active:
            action = "PROTECT"
            reason = "Tín hiệu suy yếu xuất hiện; giữ vị thế nhưng siết trailing stop."
        elif technical.signal is Signal.SELL:
            action = "WATCH"
            reason = "Tín hiệu suy yếu chưa chạm hard stop; tiếp tục quan sát với kỷ luật rủi ro."
        else:
            action = "HOLD"
            reason = "Vị thế chưa vi phạm điều kiện thoát."

        return PositionAssessment(
            symbol=str(position["symbol"]), price=price, entry_price=entry, quantity=quantity,
            pnl_pct=pnl_pct, high_watermark=peak, stop_price=round(stop, 2),
            risk_multiple=round(risk_multiple, 2), trailing_active=trailing_active,
            action=action, reason=reason, regime=technical.regime,
            technical_signal=technical.signal,
        )
