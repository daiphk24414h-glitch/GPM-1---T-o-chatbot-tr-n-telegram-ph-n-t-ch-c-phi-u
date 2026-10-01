from __future__ import annotations

from datetime import date
import json
import os
from pathlib import Path
import threading
import time
import unicodedata

import pandas as pd

from .models import FundamentalResult


WEIGHTS = {"growth": 0.35, "quality": 0.25, "health": 0.20, "valuation": 0.20}


def _higher(value: float | None, bad: float, good: float) -> float | None:
    if value is None:
        return None
    return max(0.0, min(100.0, (value - bad) / (good - bad) * 100))


def _lower(value: float | None, good: float, bad: float) -> float | None:
    if value is None:
        return None
    return max(0.0, min(100.0, (bad - value) / (bad - good) * 100))


class FundamentalEngine:
    """Scores a point-in-time normalized snapshot; fetching is intentionally separate."""

    REQUIRED = ["revenue_cagr", "eps_cagr", "industry_growth", "gdp_growth", "roe_peer_rel",
                "roic_peer_rel", "operating_margin", "de_peer_rel", "interest_coverage", "cfo_ni",
                "pe_peer_rel", "pb_peer_rel", "ev_ebitda_peer_rel"]

    def analyze(self, symbol: str, snapshot: dict | None) -> FundamentalResult:
        if not snapshot:
            return FundamentalResult(symbol, None, None, "UNAVAILABLE", missing=self.REQUIRED.copy(),
                                     source="Chưa có snapshot cơ bản point-in-time")
        if snapshot.get("schema") == "vnstock_free_ratio":
            return self._analyze_free(symbol, snapshot)
        missing = [name for name in self.REQUIRED if snapshot.get(name) is None]
        if missing:
            return FundamentalResult(symbol, snapshot.get("as_of"), None, "INCOMPLETE", missing=missing,
                                     source=str(snapshot.get("source", "")))
        growth = sum([
            _higher(snapshot["revenue_cagr"], 0, 0.20),
            _higher(snapshot["eps_cagr"], 0, 0.20),
            100.0 if snapshot["industry_growth"] > snapshot["gdp_growth"] else 0.0,
        ]) / 3
        quality = sum([
            _higher(snapshot["roe_peer_rel"], 0.8, 1.2),
            _higher(snapshot["roic_peer_rel"], 0.8, 1.2),
            _higher(snapshot["operating_margin"], 0.05, 0.20),
        ]) / 3
        health = sum([
            _lower(snapshot["de_peer_rel"], 0.7, 1.3),
            _higher(snapshot["interest_coverage"], 1.5, 5.0),
            _higher(snapshot["cfo_ni"], 0.5, 1.2),
        ]) / 3
        quality_gate = growth >= 50 and health >= 50
        valuation = sum([
            _lower(snapshot["pe_peer_rel"], 0.7, 1.3),
            _lower(snapshot["pb_peer_rel"], 0.7, 1.3),
            _lower(snapshot["ev_ebitda_peer_rel"], 0.7, 1.3),
        ]) / 3 if quality_gate else 0.0
        components = {"growth": growth, "quality": quality, "health": health, "valuation": valuation}
        score = round(sum(components[k] * WEIGHTS[k] for k in WEIGHTS), 1)
        status = "POSITIVE" if score >= 70 else "NEUTRAL" if score >= 40 else "NEGATIVE"
        return FundamentalResult(symbol, snapshot.get("as_of", date.today()), score, status,
                                 {k: round(v, 1) for k, v in components.items()}, source=str(snapshot.get("source", "")))

    def _analyze_free(self, symbol: str, snapshot: dict) -> FundamentalResult:
        metrics = snapshot.get("metrics", {})
        company_type = snapshot.get("company_type", "CT")
        def avg(values):
            clean = [v for v in values if v is not None]
            return sum(clean) / len(clean) if clean else None

        if company_type == "CK":
            model_weights = {"growth": .30, "quality": .30, "health": .15, "valuation": .25}
            growth = _higher(metrics.get("eps_cagr"), 0, .25)
            quality = avg([_higher(metrics.get("roe"), .08, .22), _higher(metrics.get("roa"), .01, .05)])
            health = _lower(metrics.get("financial_leverage"), 1.5, 6.0)
            valuation = avg([_lower(metrics.get("pe_ratio"), 8, 25), _lower(metrics.get("pb_ratio"), 1, 4)])
        elif company_type == "BH":
            model_weights = {"growth": .20, "quality": .30, "health": .25, "valuation": .25}
            growth = _higher(metrics.get("eps_cagr"), 0, .18)
            quality = avg([_higher(metrics.get("roe"), .06, .18), _higher(metrics.get("roa"), .005, .03)])
            health = _lower(metrics.get("financial_leverage"), 2.0, 8.0)
            valuation = avg([_lower(metrics.get("pe_ratio"), 8, 25), _lower(metrics.get("pb_ratio"), .8, 3.0)])
        elif company_type == "NH":
            model_weights = {"growth": .25, "quality": .30, "health": .30, "valuation": .15}
            growth = avg([_higher(metrics.get("loans_growth"), 0, .20), _higher(metrics.get("deposit_growth"), 0, .20)])
            quality = avg([_higher(metrics.get("roe"), .08, .20), _higher(metrics.get("net_interest_margin"), .02, .05)])
            health = avg([_lower(metrics.get("npl"), .01, .04), _higher(metrics.get("car"), .08, .13)])
            valuation = avg([_lower(metrics.get("pe_ratio"), 7, 18), _lower(metrics.get("pb_ratio"), .8, 2.5)])
        else:
            sector = str(metrics.get("industry") or "")
            normalized_sector = "".join(c for c in unicodedata.normalize("NFD", sector.lower())
                                        if unicodedata.category(c) != "Mn")
            normalized_sector = normalized_sector.replace("đ", "d")
            if "bat dong san" in normalized_sector:
                model_weights = {"growth": .15, "quality": .20, "health": .35, "valuation": .30}
            elif any(x in normalized_sector for x in ("tai nguyen", "dau khi", "hoa chat")):
                model_weights = {"growth": .20, "quality": .25, "health": .25, "valuation": .30}
            elif any(x in normalized_sector for x in ("dien", "nuoc", "tien ich")):
                model_weights = {"growth": .15, "quality": .25, "health": .25, "valuation": .35}
            elif any(x in normalized_sector for x in ("cong nghe", "hang tieu dung", "ban le")):
                model_weights = {"growth": .35, "quality": .30, "health": .15, "valuation": .20}
            else:
                model_weights = WEIGHTS
            growth = _higher(metrics.get("eps_cagr"), 0, .20)
            quality = avg([_higher(metrics.get("roe"), .08, .22), _higher(metrics.get("roic"), .06, .18),
                           _higher(metrics.get("ebit_margin"), .05, .20)])
            health = avg([_lower(metrics.get("debt_to_equity"), .3, 1.8), _higher(metrics.get("current_ratio"), .8, 2.0)])
            valuation = avg([_lower(metrics.get("pe_ratio"), 8, 25), _lower(metrics.get("pb_ratio"), 1, 4),
                             _lower(metrics.get("ev_to_ebitda"), 6, 18)])
        components = {"growth": growth, "quality": quality, "health": health, "valuation": valuation}
        available = {k: v for k, v in components.items() if v is not None}
        if len(available) < 3:
            missing = [k for k, v in components.items() if v is None]
            return FundamentalResult(symbol, snapshot.get("as_of"), None, "INCOMPLETE", components, missing,
                                     str(snapshot.get("source", "")), metrics)
        active_weight = sum(model_weights[k] for k in available)
        score = round(sum(available[k] * model_weights[k] for k in available) / active_weight, 1)
        metrics["model_weights"] = ", ".join(f"{k} {v:.0%}" for k, v in model_weights.items())
        status = "POSITIVE" if score >= 70 else "NEUTRAL" if score >= 40 else "NEGATIVE"
        return FundamentalResult(symbol, snapshot.get("as_of"), score, status,
                                 {k: None if v is None else round(v, 1) for k, v in components.items()}, [],
                                 str(snapshot.get("source", "")), metrics)


class FundamentalRepository:
    """Repository contract. A licensed data adapter can replace this without touching strategies."""

    def latest_as_of(self, symbol: str, as_of: date) -> dict | None:
        return None


class VnstockFreeFundamentalRepository(FundamentalRepository):
    """Current annual ratios from Vnstock Free. Not valid for point-in-time backtests."""

    META = {"item", "item_en", "item_id"}

    def __init__(self, cache_dir: Path | None = None, cache_hours: int = 12):
        self._cache: dict[str, tuple[float, dict | None]] = {}
        self._cache_lock = threading.Lock()
        self._industry_frame = None
        self.cache_dir = cache_dir
        self.cache_seconds = max(1, cache_hours) * 3600
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _disk_path(self, symbol: str) -> Path | None:
        return self.cache_dir / f"{symbol.upper()}.json" if self.cache_dir else None

    def _read_disk(self, symbol: str) -> dict | None:
        path = self._disk_path(symbol)
        try:
            if not path or not path.exists() or time.time() - path.stat().st_mtime > self.cache_seconds:
                return None
            result = json.loads(path.read_text(encoding="utf-8"))
            if result.get("as_of"):
                result["as_of"] = date.fromisoformat(result["as_of"])
            return result
        except (OSError, ValueError, TypeError):
            return None

    def _write_disk(self, symbol: str, snapshot: dict) -> None:
        path = self._disk_path(symbol)
        if not path:
            return
        temp = path.with_suffix(".tmp")
        try:
            temp.write_text(json.dumps(snapshot, ensure_ascii=False, default=str), encoding="utf-8")
            os.replace(temp, path)
        except OSError:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def _industry(self, symbol: str) -> str | None:
        try:
            with self._cache_lock:
                if self._industry_frame is None:
                    from vnstock.api.listing import Listing
                    self._industry_frame = Listing(source="VCI", random_agent=False, show_log=False).symbols_by_industries()
                frame = self._industry_frame
            rows = frame[(frame["symbol"] == symbol) & (frame["icb_level"] == 1)]
            return str(rows.iloc[0]["icb_name"]) if not rows.empty else None
        except Exception:
            return None

    @staticmethod
    def _period_columns(frame: pd.DataFrame) -> list:
        def key(value):
            text = str(value)
            digits = "".join(c for c in text if c.isdigit())
            return int(digits[:4]) if len(digits) >= 4 else -1
        return sorted([c for c in frame.columns if c not in VnstockFreeFundamentalRepository.META], key=key)

    @staticmethod
    def _series(frame: pd.DataFrame, item_id: str) -> list[float]:
        rows = frame[frame["item_id"].astype(str).str.lower() == item_id]
        if rows.empty:
            return []
        values = pd.to_numeric(rows.iloc[0][VnstockFreeFundamentalRepository._period_columns(frame)], errors="coerce")
        return [float(v) for v in values.dropna() if float(v) != 0]

    @classmethod
    def _latest(cls, frame: pd.DataFrame, item_id: str, percent: bool = False) -> float | None:
        values = cls._series(frame, item_id)
        if not values:
            return None
        value = values[-1]
        if percent and abs(value) > 1:
            value /= 100
        return value

    @classmethod
    def _cagr(cls, frame: pd.DataFrame, item_id: str) -> float | None:
        values = cls._series(frame, item_id)
        values = values[-4:]
        if len(values) < 3 or values[0] <= 0 or values[-1] <= 0:
            return None
        return (values[-1] / values[0]) ** (1 / (len(values) - 1)) - 1

    def latest_as_of(self, symbol: str, as_of: date) -> dict | None:
        with self._cache_lock:
            cached = self._cache.get(symbol)
            if cached and time.monotonic() - cached[0] < 3600:
                return cached[1]
        disk_cached = self._read_disk(symbol)
        if disk_cached is not None:
            with self._cache_lock:
                self._cache[symbol] = (time.monotonic(), disk_cached)
            return disk_cached
        try:
            from vnstock.api.financial import Finance
            finance = Finance(source="VCI", symbol=symbol, period="year", show_log=False)
            ratio = finance.ratio(period="year", lang="en", dropna=True, show_log=False)
            if ratio.empty or "item_id" not in ratio.columns:
                return None
            pct = {"roe", "roa", "roic", "ebit_margin", "loans_growth", "deposit_growth", "net_interest_margin", "npl", "car"}
            ids = ["pe_ratio", "pb_ratio", "ev_to_ebitda", "roe", "roic", "ebit_margin",
                   "debt_to_equity", "current_ratio", "loans_growth", "deposit_growth",
                   "net_interest_margin", "npl", "car", "roa", "financial_leverage"]
            metrics = {item: self._latest(ratio, item, item in pct) for item in ids}
            metrics["eps_cagr"] = self._cagr(ratio, "earnings_per_share")
            metrics["industry"] = self._industry(symbol)
            metrics["company_type"] = getattr(finance.provider, "com_type_code", "CT")
            periods = self._period_columns(ratio)
            metrics["financial_period"] = str(periods[-1]) if periods else "UNKNOWN"
            metrics["retrieved_at"] = as_of.isoformat()
            result = {"schema": "vnstock_free_ratio", "as_of": as_of,
                    "company_type": getattr(finance.provider, "com_type_code", "CT"), "metrics": metrics,
                    "source": "Vnstock Free/VCI annual ratios; current snapshot, not point-in-time"}
            with self._cache_lock:
                self._cache[symbol] = (time.monotonic(), result)
            self._write_disk(symbol, result)
            return result
        except Exception:
            return None
