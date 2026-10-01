from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from .technical import indicators


def price_chart(df: pd.DataFrame, symbol: str, target: Path) -> Path:
    calc = indicators(df).tail(150)
    target.parent.mkdir(parents=True, exist_ok=True)
    fig, (ax, vol) = plt.subplots(2, 1, figsize=(11, 7), sharex=True, gridspec_kw={"height_ratios": [4, 1]})
    ax.plot(calc.time, calc.close, label="Close", color="#111827", linewidth=1.4)
    ax.plot(calc.time, calc.ema20, label="EMA20", color="#2563eb", linewidth=1)
    ax.plot(calc.time, calc.ema50, label="EMA50", color="#f97316", linewidth=1)
    ax.fill_between(calc.time, calc.bb_lower, calc.bb_upper, color="#94a3b8", alpha=.16, label="Bollinger")
    colors = ["#16a34a" if c >= o else "#dc2626" for c, o in zip(calc.close, calc.open)]
    vol.bar(calc.time, calc.volume, color=colors, width=1)
    ax.set_title(f"{symbol.upper()} — giá và chỉ báo")
    ax.grid(alpha=.15); vol.grid(alpha=.1); ax.legend(ncol=4, fontsize=8)
    fig.tight_layout(); fig.savefig(target, dpi=150); plt.close(fig)
    return target


def equity_chart(equity: pd.DataFrame, symbol: str, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(equity.date, equity.equity, color="#2563eb", linewidth=1.5)
    ax.set_title(f"Equity curve — {symbol.upper()}"); ax.grid(alpha=.2)
    fig.tight_layout(); fig.savefig(target, dpi=150); plt.close(fig)
    return target


def backtest_chart(prices: pd.DataFrame, result, symbol: str, target: Path) -> Path:
    """Price with actual next-open fills and the corresponding equity curve."""
    target.parent.mkdir(parents=True, exist_ok=True)
    start = pd.Timestamp(result.equity.date.min())
    view = prices[pd.to_datetime(prices.time) >= start].copy()
    fig, (ax_price, ax_equity) = plt.subplots(
        2, 1, figsize=(12, 8), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    ax_price.plot(view.time, view.close, color="#0f172a", linewidth=1.15, label="Close")
    log = result.trade_log
    if not log.empty:
        ax_price.scatter(pd.to_datetime(log.entry_date), log.entry, marker="^", s=58,
                         color="#16a34a", edgecolor="white", linewidth=.5, label="BUY — next open", zorder=4)
        styles = {
            "HARD_STOP": ("#dc2626", "X", "SELL — hard stop"),
            "TRAILING_STOP": ("#2563eb", "v", "SELL — trailing stop"),
            "CONFIRMED_SIGNAL_EXIT": ("#f97316", "v", "SELL — confirmed signal"),
            "NEXT_OPEN_SELL": ("#f97316", "v", "SELL — signal"),
            "END_OF_TEST": ("#64748b", "s", "SELL — end"),
        }
        for reason, rows in log.groupby("reason"):
            color, marker, label = styles.get(reason, ("#dc2626", "v", f"SELL — {reason}"))
            ax_price.scatter(pd.to_datetime(rows.exit_date), rows.exit, marker=marker, s=52,
                             color=color, edgecolor="white", linewidth=.5, label=label, zorder=4)
        for number, row in enumerate(log.itertuples(), 1):
            ax_price.annotate(str(number), (pd.Timestamp(row.entry_date), row.entry), xytext=(0, 7),
                              textcoords="offset points", ha="center", fontsize=6, color="#166534")
            ax_price.annotate(str(number), (pd.Timestamp(row.exit_date), row.exit), xytext=(0, -11),
                              textcoords="offset points", ha="center", fontsize=6, color="#991b1b")
    ax_price.set_title(f"{symbol.upper()} — điểm khớp lệnh lịch sử của chiến lược")
    ax_price.set_ylabel("Giá")
    ax_price.grid(alpha=.18)
    handles, labels = ax_price.get_legend_handles_labels()
    unique = dict(zip(labels, handles))
    ax_price.legend(unique.values(), unique.keys(), ncol=3, fontsize=8, loc="best")
    ax_equity.plot(result.equity.date, result.equity.equity, color="#2563eb", linewidth=1.35)
    ax_equity.set_ylabel("Equity")
    ax_equity.set_title("Đường vốn sau phí, thuế và trượt giá", fontsize=10)
    ax_equity.grid(alpha=.18)
    fig.tight_layout()
    fig.savefig(target, dpi=160)
    plt.close(fig)
    return target
