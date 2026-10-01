from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .models import Regime, Signal, TechnicalResult


def indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    close = out["close"]
    out["ema20"] = close.ewm(span=20, adjust=False).mean()
    out["ema50"] = close.ewm(span=50, adjust=False).mean()
    out["ema200"] = close.ewm(span=200, adjust=False).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    out["rsi14"] = 100 - 100 / (1 + rs)
    out.loc[(loss == 0) & (gain > 0), "rsi14"] = 100.0
    out.loc[(gain == 0) & (loss > 0), "rsi14"] = 0.0
    out["sma20"] = close.rolling(20).mean()
    std = close.rolling(20).std()
    out["bb_upper"] = out["sma20"] + 2 * std
    out["bb_lower"] = out["sma20"] - 2 * std
    out["bb_width"] = (out["bb_upper"] - out["bb_lower"]) / out["sma20"]
    out["bb_width_median120"] = out["bb_width"].rolling(120, min_periods=40).median()
    out["vol_ma20"] = out["volume"].rolling(20).mean()
    out["rvol20"] = out["volume"] / out["vol_ma20"].replace(0, np.nan)
    previous = close.shift(1)
    tr = pd.concat([(out.high - out.low), (out.high - previous).abs(), (out.low - previous).abs()], axis=1).max(axis=1)
    out["atr14"] = tr.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    out["resistance20"] = out["high"].shift(1).rolling(20).max()
    out["return21"] = close.pct_change(21)
    out["return63"] = close.pct_change(63)
    out["return126"] = close.pct_change(126)
    out["atr_pct"] = out["atr14"] / close.replace(0, np.nan)
    out["turnover20"] = (close * out["volume"]).rolling(20).mean()
    out["turnover_median126"] = out["turnover20"].rolling(126, min_periods=60).median()
    return out


def market_regime(vnindex: pd.DataFrame) -> Regime:
    if len(vnindex) < 200:
        return Regime.UNKNOWN
    row = indicators(vnindex).iloc[-1]
    if row[["close", "ema20", "ema50", "ema200", "rsi14"]].isna().any():
        return Regime.UNKNOWN
    if row.close > row.ema50 and row.ema20 > row.ema50 and row.close > row.ema200 and row.rsi14 > 50:
        return Regime.UP
    if row.close < row.ema50 and row.ema20 < row.ema50 and row.rsi14 < 50:
        return Regime.DOWN
    return Regime.SIDEWAY


class TechnicalEngine:
    def __init__(self, capital: float = 100_000_000, risk_per_trade: float = 0.006,
                 max_allocation: float = 0.15, lot_size: int = 1):
        self.capital = capital
        self.risk_per_trade = risk_per_trade
        self.max_allocation = max_allocation
        self.lot_size = max(1, int(lot_size))

    @staticmethod
    def _relative_strength(prices: pd.DataFrame, benchmark: pd.DataFrame | None) -> float:
        """Score excess 3/6-month performance without peeking past the last bar."""
        if benchmark is None or len(prices) < 127 or len(benchmark) < 127:
            return 50.0
        stock = prices.set_index(pd.to_datetime(prices.time)).close.astype(float)
        index = benchmark.set_index(pd.to_datetime(benchmark.time)).close.astype(float)
        aligned = pd.concat([stock.rename("stock"), index.rename("index")], axis=1).dropna()
        if len(aligned) < 127:
            return 50.0
        excess63 = (aligned["stock"].iloc[-1] / aligned["stock"].iloc[-64]
                    - aligned["index"].iloc[-1] / aligned["index"].iloc[-64])
        excess126 = (aligned["stock"].iloc[-1] / aligned["stock"].iloc[-127]
                     - aligned["index"].iloc[-1] / aligned["index"].iloc[-127])
        return float(np.clip(50 + 180 * (.6 * excess63 + .4 * excess126), 0, 100))

    def analyze(self, symbol: str, prices: pd.DataFrame, regime: Regime,
                benchmark: pd.DataFrame | None = None) -> TechnicalResult:
        if len(prices) < 200 or regime is Regime.UNKNOWN:
            return TechnicalResult(symbol, pd.Timestamp(prices.iloc[-1].time).date(), Signal.BLOCKED, regime,
                                   "NONE", 0, float(prices.iloc[-1].close), None, None, 0,
                                   ["Thiếu dữ liệu đáng tin cậy hoặc chưa xác định được trạng thái VNINDEX."])
        calc = indicators(prices)
        row, prev = calc.iloc[-1], calc.iloc[-2]
        needed = ["ema20", "ema50", "ema200", "rsi14", "sma20", "bb_upper", "bb_lower",
                  "rvol20", "atr14", "atr_pct", "resistance20", "return63", "return126",
                  "turnover20", "turnover_median126"]
        if row[needed].isna().any():
            return TechnicalResult(symbol, pd.Timestamp(row.time).date(), Signal.BLOCKED, regime,
                                   "NONE", 0, float(row.close), None, None, 0, ["Chỉ báo chưa đủ dữ liệu."])

        relative_strength = self._relative_strength(prices, benchmark)
        trend = 25.0 * sum((row.close > row.ema20, row.ema20 > row.ema50,
                            row.ema50 > row.ema200, row.ema20 > prev.ema20))
        momentum = float(np.clip(100 - abs(float(row.rsi14) - 58) * 4.5, 0, 100))
        expanding = row.bb_width > row.bb_width_median120 if not math.isnan(row.bb_width_median120) else False
        band_span = max(float(row.bb_upper - row.bb_lower), 1e-9)
        band_position = float(np.clip((row.close - row.bb_lower) / band_span, 0, 1))
        if regime is Regime.SIDEWAY:
            entry_quality = float(np.clip(100 - band_position * 100 + max(0, 45 - row.rsi14), 0, 100))
        else:
            distance20 = abs(float(row.close / row.ema20 - 1))
            entry_quality = float(np.clip(100 - distance20 * 900, 0, 100))
            if row.close > row.resistance20:
                entry_quality = min(100.0, entry_quality + 15)
        volume = float(np.clip(row.rvol20 / 1.5 * 100, 0, 100))
        atr_pct = float(row.atr_pct)
        risk_score = 100.0 if .015 <= atr_pct <= .05 else (65.0 if .008 <= atr_pct <= .07 else 30.0)
        turnover_ratio = float(row.turnover20 / row.turnover_median126) if row.turnover_median126 > 0 else 0.0
        liquidity_score = float(np.clip(turnover_ratio / 1.2 * 100, 0, 100))
        risk_liquidity = (risk_score + liquidity_score) / 2
        # 80% technical block: RS 25, trend 20, entry 15, volume 10, risk/liquidity 10.
        # Values below are normalized inside that block and therefore sum to one.
        components = {"relative_strength": relative_strength, "trend": trend,
                      "entry_quality": entry_quality, "volume": volume,
                      "risk_liquidity": risk_liquidity, "momentum": momentum}
        weights = {"relative_strength": .3125, "trend": .25, "entry_quality": .1875,
                   "volume": .125, "risk_liquidity": .125}
        score = round(sum(components[k] * weights[k] for k in weights), 1)

        breakout = (regime is Regime.UP and relative_strength >= 55 and row.close > row.resistance20
                    and row.rvol20 >= 1.5 and 50 <= row.rsi14 < 70 and expanding)
        pullback = (regime is Regime.UP and relative_strength >= 50 and row.ema20 > row.ema50
                    and row.low <= row.ema20 * 1.01 and row.close > row.ema20 and row.close > row.open
                    and 45 <= row.rsi14 < 72 and row.rvol20 >= 1)
        mean_reversion = regime is Regime.SIDEWAY and row.low <= row.bb_lower and row.close > row.bb_lower and row.rsi14 < 40 and row.close > row.open
        scored_uptrend = (regime is Regime.UP and score >= 62 and row.close > row.ema20
                          and relative_strength >= 50 and 45 <= row.rsi14 < 72 and row.rvol20 >= .8)
        scored_sideway = (regime is Regime.SIDEWAY and score >= 55 and row.low <= row.bb_lower * 1.02
                          and row.close > row.bb_lower and row.rsi14 < 45 and row.rvol20 >= .7)
        sell = row.close < row.sma20 or row.close < row.bb_lower or (prev.rsi14 > 70 and row.rsi14 < prev.rsi14)
        # Entry setups take precedence over a generic close<SMA20 warning. The
        # safety gates (market regime, trend, momentum and liquidity) remain
        # mandatory; the remaining evidence is ranked with a continuous score.
        if regime is Regime.DOWN and sell:
            signal, strategy, reasons = Signal.SELL, "MARKET RISK EXIT", ["Giá suy yếu đồng thời VNINDEX ở trạng thái DOWN."]
        elif regime is Regime.DOWN:
            signal, strategy, reasons = Signal.HOLD, "MARKET RISK FILTER", ["VNINDEX đang ở chế độ DOWN; khóa vị thế mua mới."]
        elif breakout:
            signal, strategy, reasons = Signal.BUY, "UPTREND BREAKOUT", ["Vượt kháng cự 20 phiên với RVOL và Bollinger BandWidth xác nhận."]
        elif pullback:
            signal, strategy, reasons = Signal.BUY, "UPTREND PULLBACK", ["Pullback EMA20 và phục hồi có khối lượng xác nhận."]
        elif mean_reversion:
            signal, strategy, reasons = Signal.BUY, "SIDEWAY MEAN REVERSION", ["Chạm dải dưới rồi đóng cửa hồi phục trong thị trường sideway."]
        elif scored_uptrend:
            signal, strategy, reasons = Signal.BUY, "UPTREND RANKED SETUP", ["Điểm hợp lưu kỹ thuật vượt ngưỡng trong xu hướng tăng; thanh khoản và động lượng đạt cổng an toàn."]
        elif scored_sideway:
            signal, strategy, reasons = Signal.BUY, "SIDEWAY RANKED SETUP", ["Giá ở vùng thấp của biên dao động và điểm hợp lưu đạt ngưỡng mean-reversion."]
        elif sell:
            signal, strategy, reasons = Signal.SELL, "RISK WARNING", ["Giá hoặc động lượng suy yếu; với vị thế đang lãi, ưu tiên siết trailing stop trước khi thoát hoàn toàn."]
        else:
            signal, strategy, reasons = Signal.HOLD, "NONE", ["Chưa có setup đạt đủ điều kiện."]

        stop = max(row.close * 0.93, row.close - 2 * row.atr14)
        risk = row.close - stop
        shares = (int(min(self.capital * self.risk_per_trade / risk,
                          self.capital * self.max_allocation / row.close) // self.lot_size) * self.lot_size
                  if risk > 0 else 0)
        take = row.close + 2 * risk
        values = {name: round(float(row[name]), 3) for name in needed}
        return TechnicalResult(symbol, pd.Timestamp(row.time).date(), signal, regime, strategy, score,
                               float(row.close), round(float(stop), 2), round(float(take), 2), shares,
                               reasons, values, {k: round(v, 1) for k, v in components.items()})
