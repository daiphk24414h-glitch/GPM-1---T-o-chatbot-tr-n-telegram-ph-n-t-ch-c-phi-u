from dataclasses import dataclass

import numpy as np
import pandas as pd

from .models import Regime
from .technical import indicators, market_regime


@dataclass(slots=True)
class MarketOutlook:
    regime: Regime
    bias: str
    score: float
    confidence: str
    horizon: str
    support: float
    resistance: float
    components: dict[str, float]
    weights: dict[str, float]
    drivers: list[str]
    exposure_min: int
    exposure_max: int


def analyze_market(vnindex: pd.DataFrame) -> MarketOutlook:
    if len(vnindex) < 200:
        raise ValueError("Cần tối thiểu 200 phiên VNINDEX để đánh giá thị trường")
    calc = indicators(vnindex)
    row, prev = calc.iloc[-1], calc.iloc[-2]
    regime = market_regime(vnindex)
    if regime is Regime.UNKNOWN:
        raise ValueError("Không xác định được trạng thái VNINDEX")

    trend = np.mean([
        100 if row.close > row.ema20 else -100,
        100 if row.ema20 > row.ema50 else -100,
        100 if row.close > row.ema200 else -100,
        100 if row.ema20 > prev.ema20 else -100,
    ])
    momentum = float(np.clip((row.rsi14 - 50) * 4, -100, 100))
    band_span = max(float(row.bb_upper - row.bb_lower), 1e-9)
    bollinger = float(np.clip((row.close - row.sma20) / (band_span / 2) * 100, -100, 100))
    direction = 1 if row.close >= prev.close else -1
    volume = float(np.clip(direction * row.rvol20 / 1.5 * 100, -100, 100))
    weights = {
        Regime.UP: {"trend": .35, "momentum": .25, "bollinger": .15, "volume": .25},
        Regime.SIDEWAY: {"trend": .15, "momentum": .20, "bollinger": .45, "volume": .20},
        Regime.DOWN: {"trend": .40, "momentum": .25, "bollinger": .15, "volume": .20},
    }[regime]
    components = {"trend": float(trend), "momentum": momentum, "bollinger": bollinger, "volume": volume}
    score = round(sum(components[k] * weights[k] for k in weights), 1)
    bias = "nghiêng tăng" if score >= 20 else "nghiêng giảm" if score <= -20 else "trung tính"
    strength = abs(score)
    confidence = "cao" if strength >= 65 else "trung bình" if strength >= 35 else "thấp"
    drivers = [
        f"EMA20 {'trên' if row.ema20 > row.ema50 else 'dưới'} EMA50",
        f"VNINDEX {'trên' if row.close > row.ema200 else 'dưới'} EMA200",
        f"RSI14 ở {row.rsi14:.1f}",
        f"RVOL20 ở {row.rvol20:.2f}",
        f"Bollinger BandWidth {'trên' if row.bb_width > row.bb_width_median120 else 'dưới'} trung vị 120 phiên",
    ]
    if regime is Regime.UP and score >= 50:
        exposure = (80, 100)
    elif regime is Regime.UP:
        exposure = (60, 75)
    elif regime is Regime.SIDEWAY:
        exposure = (35, 60)
    else:
        exposure = (0, 25)
    return MarketOutlook(regime, bias, score, confidence, "5–20 phiên",
                         float(calc.low.tail(20).min()), float(calc.high.tail(20).max()),
                         {k: round(v, 1) for k, v in components.items()}, weights, drivers,
                         *exposure)
