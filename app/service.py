from __future__ import annotations

from datetime import date
from pathlib import Path

from .data import MarketDataService
from .fundamental import FundamentalEngine, FundamentalRepository, VnstockFreeFundamentalRepository
from .models import Signal
from .technical import TechnicalEngine, market_regime


class AnalysisService:
    def __init__(self, data: MarketDataService, fundamentals: FundamentalRepository | None = None,
                 fundamental_cache_dir: Path | None = None, cache_hours: int = 12):
        self.data = data
        self.fundamentals = fundamentals or VnstockFreeFundamentalRepository(fundamental_cache_dir, cache_hours)
        self.technical = TechnicalEngine()
        self.fundamental = FundamentalEngine()

    def analyze(self, symbol: str, benchmark=None):
        prices = self.data.history(symbol, 500)
        benchmark = benchmark if benchmark is not None else self.data.history("VNINDEX", 500)
        self.data.assert_fresh(prices)
        self.data.assert_fresh(benchmark)
        tech = self.technical.analyze(symbol.upper(), prices, market_regime(benchmark), benchmark)
        snapshot = self.fundamentals.latest_as_of(symbol.upper(), date.today())
        fund = self.fundamental.analyze(symbol.upper(), snapshot)
        final = self.combine_signal(tech, fund)
        return prices, tech, fund, final

    @staticmethod
    def combine_signal(tech, fund):
        final = tech.signal
        if tech.signal is Signal.BUY:
            combined = None if fund.score is None else .8 * tech.score + .2 * fund.score
            # Fundamentals are a quality gate; they never manufacture a BUY when
            # the technical setup is absent. Missing/special-sector data blocks it.
            if fund.score is None or fund.score < 40 or combined < 60:
                final = Signal.BLOCKED
        return final
