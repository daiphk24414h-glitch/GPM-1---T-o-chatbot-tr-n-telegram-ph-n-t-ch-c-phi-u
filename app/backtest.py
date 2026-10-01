from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .models import Regime, Signal
from .technical import TechnicalEngine, indicators, market_regime


@dataclass(slots=True)
class BacktestResult:
    initial_capital: float
    final_equity: float
    total_return_pct: float
    cagr_pct: float
    max_drawdown_pct: float
    sharpe: float
    sortino: float
    win_rate_pct: float
    profit_factor: float
    trades: int
    benchmark_return_pct: float
    alpha_pct: float
    calmar: float
    average_exposure_pct: float
    expectancy_pct: float
    buy_signal_sessions: int
    rejected_entries: int
    equity: pd.DataFrame
    trade_log: pd.DataFrame


class BacktestEngine:
    def __init__(self, fee_rate=0.0015, sell_tax=0.001, slippage=0.001,
                 min_hold_sessions=3, sell_confirmation_sessions=2, lot_size=1):
        self.fee_rate, self.sell_tax, self.slippage = fee_rate, sell_tax, slippage
        self.min_hold_sessions = min_hold_sessions
        self.sell_confirmation_sessions = sell_confirmation_sessions
        self.lot_size = max(1, int(lot_size))

    def _position_quantity(self, equity: float, cash: float, fill: float,
                           risk_distance: float) -> int:
        risk_qty = equity * .006 / max(risk_distance, fill * .01)
        cap_qty = equity * .15 / fill
        affordable = cash / (fill * (1 + self.fee_rate))
        return int(min(risk_qty, cap_qty, affordable) // self.lot_size) * self.lot_size

    def run(self, symbol: str, prices: pd.DataFrame, benchmark: pd.DataFrame,
            fundamental_pass: bool = True, initial_capital: float = 100_000_000,
            evaluation_start=None) -> BacktestResult:
        if len(prices) < 220 or len(benchmark) < 220:
            raise ValueError("Backtest cần tối thiểu 220 phiên cho cổ phiếu và VNINDEX")
        cash, shares, entry, stop = initial_capital, 0, 0.0, 0.0
        entry_date = None
        pending_signal = None
        pending_risk = 0.0
        pending_time_exit = False
        entry_risk = 0.0
        holding_sessions = 0
        sell_streak = 0
        high_watermark = 0.0
        trailing_active = False
        trades, curve = [], []
        buy_signal_sessions = rejected_entries = 0
        bench = benchmark.set_index(pd.to_datetime(benchmark.time)).sort_index()
        start_day = pd.Timestamp(evaluation_start) if evaluation_start is not None else None
        for i in range(200, len(prices)):
            history = prices.iloc[: i + 1]
            current = history.iloc[-1]
            day = pd.Timestamp(current.time)
            open_price, high, low, close = map(float, [current.open, current.high, current.low, current.close])
            in_evaluation = start_day is None or day >= start_day

            # A signal can only be executed on the next session. This avoids
            # using today's close and full-day volume to buy at today's close.
            if (in_evaluation and shares > 0 and
                    ((pending_time_exit and holding_sessions >= 20) or
                     (pending_signal is Signal.SELL
                      and holding_sessions >= self.min_hold_sessions
                      and sell_streak >= self.sell_confirmation_sessions
                      and not trailing_active))):
                fill = open_price * (1 - self.slippage)
                proceeds = shares * fill * (1 - self.fee_rate - self.sell_tax)
                pnl = proceeds - shares * entry * (1 + self.fee_rate)
                cash += proceeds
                exit_reason = "TIME_STOP" if pending_time_exit else "CONFIRMED_SIGNAL_EXIT"
                trades.append({"entry_date": entry_date, "exit_date": day, "entry": entry,
                               "exit": fill, "shares": shares, "pnl": pnl, "reason": exit_reason})
                shares, entry, entry_date = 0, 0.0, None
                entry_risk, pending_time_exit = 0.0, False
                holding_sessions, sell_streak, high_watermark, trailing_active = 0, 0, 0.0, False
            elif in_evaluation and shares == 0 and pending_signal is Signal.BUY and fundamental_pass:
                fill = open_price * (1 + self.slippage)
                equity = cash
                risk_distance = max(pending_risk, fill * .01)
                qty = self._position_quantity(equity, cash, fill, risk_distance)
                if qty >= self.lot_size:
                    cash -= qty * fill * (1 + self.fee_rate)
                    shares, entry, entry_date = qty, fill, day
                    entry_risk = risk_distance
                    stop = max(fill * .93, fill - risk_distance)
                    high_watermark, trailing_active = fill, False
                    holding_sessions, sell_streak = 0, 0
                else:
                    rejected_entries += 1

            # Intraday protection uses the bar high/low. If both levels are hit
            # in one daily bar, assume the stop was hit first (conservative).
            if in_evaluation and shares > 0 and low <= stop:
                fill = (open_price if open_price <= stop else stop) * (1 - self.slippage)
                proceeds = shares * fill * (1 - self.fee_rate - self.sell_tax)
                pnl = proceeds - shares * entry * (1 + self.fee_rate)
                cash += proceeds
                reason = "TRAILING_STOP" if trailing_active else "HARD_STOP"
                trades.append({"entry_date": entry_date, "exit_date": day, "entry": entry,
                               "exit": fill, "shares": shares, "pnl": pnl, "reason": reason})
                shares, entry, entry_date = 0, 0.0, None
                entry_risk, pending_time_exit = 0.0, False
                holding_sessions, sell_streak, high_watermark, trailing_active = 0, 0, 0.0, False

            vni = bench.loc[:day].reset_index(drop=True)
            regime = market_regime(vni) if len(vni) >= 200 else Regime.UNKNOWN
            equity_now = cash + shares * close
            engine = TechnicalEngine(equity_now)
            result = engine.analyze(symbol, history, regime, vni)
            if in_evaluation and result.signal is Signal.BUY:
                buy_signal_sessions += 1
            pending_signal = result.signal
            pending_risk = max(result.price - (result.stop_loss or result.price * .93), 0.0)
            if shares > 0:
                holding_sessions += 1
                sell_streak = sell_streak + 1 if result.signal is Signal.SELL else 0
                high_watermark = max(high_watermark, high)
                initial_risk = max(entry_risk, entry * .01)
                if (high_watermark / entry - 1) >= .05 and (high_watermark - entry) >= initial_risk:
                    trailing_active = True
                if trailing_active:
                    multiplier = {Regime.UP: 3.25, Regime.SIDEWAY: 2.0,
                                  Regime.DOWN: 1.5, Regime.UNKNOWN: 2.0}[regime]
                    if result.signal is Signal.SELL:
                        multiplier = min(multiplier, 2.5 if regime is Regime.UP else 1.8)
                    atr = max(float(result.indicators.get("atr14") or 0), close * .01)
                    candidate = high_watermark - multiplier * atr
                    if (high_watermark - entry) >= initial_risk:
                        candidate = max(candidate, entry * (1 + self.fee_rate + self.sell_tax + self.slippage))
                    # This stop is calculated after today's close and can only
                    # take effect from the next session, avoiding look-ahead.
                    stop = max(stop, candidate)
                pending_time_exit = (holding_sessions >= 20 and
                                     (high_watermark - entry) < .5 * initial_risk)
            else:
                holding_sessions, sell_streak, high_watermark, trailing_active = 0, 0, 0.0, False
                pending_time_exit = False
            if in_evaluation:
                equity_value = cash + shares * close
                curve.append({"date": day, "equity": equity_value,
                              "exposure_pct": shares * close / equity_value * 100 if equity_value else 0.0})
        if not curve:
            raise ValueError("Khoảng backtest không có phiên giao dịch phù hợp")
        if shares:
            close = float(prices.iloc[-1].close)
            fill = close * (1 - self.slippage)
            proceeds = shares * fill * (1 - self.fee_rate - self.sell_tax)
            pnl = proceeds - shares * entry * (1 + self.fee_rate)
            cash += proceeds
            trades.append({"entry_date": entry_date, "exit_date": pd.Timestamp(prices.iloc[-1].time),
                           "entry": entry, "exit": fill, "shares": shares, "pnl": pnl, "reason": "END_OF_TEST"})
            curve[-1]["equity"] = cash
        eq = pd.DataFrame(curve)
        log = pd.DataFrame(trades, columns=["entry_date", "exit_date", "entry", "exit", "shares", "pnl", "reason"])
        if not log.empty:
            invested = log["shares"] * log["entry"] * (1 + self.fee_rate)
            log["return_pct"] = log["pnl"] / invested * 100
            log["holding_days"] = (pd.to_datetime(log["exit_date"]) - pd.to_datetime(log["entry_date"])).dt.days
        returns = eq.equity.pct_change().dropna()
        years = max((eq.date.iloc[-1] - eq.date.iloc[0]).days / 365.25, 1 / 252)
        total = cash / initial_capital - 1
        drawdown = eq.equity / eq.equity.cummax() - 1
        rf_daily = 0.065 / 252
        excess = returns - rf_daily
        sharpe = np.sqrt(252) * excess.mean() / returns.std() if returns.std() > 0 else 0.0
        downside = excess[excess < 0].std()
        sortino = np.sqrt(252) * excess.mean() / downside if downside and downside > 0 else 0.0
        wins = log[log.pnl > 0].pnl if not log.empty else pd.Series(dtype=float)
        losses = log[log.pnl < 0].pnl if not log.empty else pd.Series(dtype=float)
        pf = float(wins.sum() / abs(losses.sum())) if abs(losses.sum()) > 0 else (float("inf") if wins.sum() > 0 else 0.0)
        evaluation_benchmark = benchmark[pd.to_datetime(benchmark.time) >= pd.Timestamp(eq.date.iloc[0])]
        benchmark_return = 0.0
        if len(evaluation_benchmark) >= 2:
            benchmark_return = (float(evaluation_benchmark.iloc[-1].close) /
                                float(evaluation_benchmark.iloc[0].close) - 1) * 100
        cagr = ((cash / initial_capital) ** (1 / years) - 1) * 100
        max_dd = abs(drawdown.min()) * 100
        expectancy = float(log.return_pct.mean()) if not log.empty else 0.0
        return BacktestResult(initial_capital, cash, total * 100, ((cash / initial_capital) ** (1 / years) - 1) * 100,
                              max_dd, float(sharpe), float(sortino),
                              len(wins) / len(log) * 100 if len(log) else 0.0, pf, len(log),
                              benchmark_return, total * 100 - benchmark_return,
                              cagr / max_dd if max_dd > 0 else 0.0,
                              float(eq.exposure_pct.mean()), expectancy,
                              buy_signal_sessions, rejected_entries, eq, log)
