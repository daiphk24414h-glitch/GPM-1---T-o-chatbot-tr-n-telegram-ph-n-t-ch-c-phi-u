from __future__ import annotations

import re
import contextlib
import io
import logging
import os
import threading
import time
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd


REQUIRED = ["time", "open", "high", "low", "close", "volume"]
log = logging.getLogger(__name__)


class DataError(RuntimeError):
    pass


def _number(value):
    if pd.isna(value):
        return np.nan
    text = str(value).strip().replace(",", "")
    match = re.fullmatch(r"([-+]?\d*\.?\d+)\s*([KMB]?)", text, re.I)
    if not match:
        return pd.to_numeric(text, errors="coerce")
    scale = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
    return float(match.group(1)) * scale[match.group(2).upper()]


def normalize_ohlcv(raw: pd.DataFrame) -> pd.DataFrame:
    aliases = {
        "date": "time", "time": "time", "ngày": "time", "tradingdate": "time",
        "open": "open", "mở": "open", "mở cửa": "open", "giamocua": "open",
        "high": "high", "cao": "high", "cao nhất": "high", "giacaonhat": "high",
        "low": "low", "thấp": "low", "thấp nhất": "low", "giathapnhat": "low",
        "close": "close", "lần cuối": "close", "đóng cửa": "close", "giadongcua": "close",
        "volume": "volume", "vol": "volume", "kl": "volume", "khối lượng": "volume",
    }
    columns = {c: aliases.get(str(c).strip().lower(), str(c).strip().lower()) for c in raw.columns}
    df = raw.rename(columns=columns).copy()
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise DataError("Thiếu cột OHLCV: " + ", ".join(missing))
    df = df[REQUIRED]
    time_text = df["time"].astype(str).str.strip()
    iso_mask = time_text.str.match(r"^\d{4}-\d{2}-\d{2}")
    parsed_time = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
    parsed_time.loc[iso_mask] = pd.to_datetime(time_text.loc[iso_mask], format="ISO8601", errors="coerce")
    parsed_time.loc[~iso_mask] = pd.to_datetime(time_text.loc[~iso_mask], dayfirst=True, errors="coerce")
    df["time"] = parsed_time
    for col in REQUIRED[1:]:
        df[col] = df[col].map(_number)
    df = df.dropna(subset=REQUIRED).drop_duplicates("time", keep="last").sort_values("time")
    if df.empty:
        raise DataError("Không còn dữ liệu hợp lệ sau chuẩn hóa")
    invalid = (df["low"] > df["high"]) | (df["volume"] < 0) | (df[["open", "high", "low", "close"]] <= 0).any(axis=1)
    if invalid.any():
        raise DataError(f"Có {int(invalid.sum())} dòng OHLCV bất hợp lệ")
    return df.reset_index(drop=True)


class MarketDataService:
    def __init__(self, vnindex_csv: Path, source: str = "VCI", ssi_client=None,
                 ssi_primary: bool = True, cache_dir: Path | None = None,
                 cache_hours: int = 12, fallback_interval_seconds: float = 3.2):
        self.vnindex_csv = vnindex_csv
        self.source = source
        self.ssi = ssi_client
        self.ssi_primary = ssi_primary
        self.cache_dir = cache_dir or (vnindex_csv.parent / ".market_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_seconds = max(1, cache_hours) * 3600
        self.fallback_interval_seconds = max(0.0, fallback_interval_seconds)
        self._fallback_lock = threading.Lock()
        self._last_fallback_request = 0.0
        self._cache: dict[tuple[str, int], tuple[float, pd.DataFrame]] = {}
        self._group_cache: dict[str, tuple[float, list[str]]] = {}
        self._industry_cache: tuple[float, pd.DataFrame] | None = None
        self._cache_lock = threading.Lock()

    def _disk_path(self, symbol: str) -> Path:
        safe = re.sub(r"[^A-Z0-9_-]", "_", symbol.upper())
        return self.cache_dir / f"{safe}.csv"

    @staticmethod
    def _completed_sessions(frame: pd.DataFrame) -> pd.DataFrame:
        """Never confirm a daily signal with today's still-forming candle."""
        now = datetime.now()
        if (not frame.empty and pd.Timestamp(frame.iloc[-1].time).date() == now.date()
                and now.weekday() < 5 and (now.hour, now.minute) < (15, 10)):
            frame = frame.iloc[:-1].reset_index(drop=True)
            if frame.empty:
                raise DataError("Chưa có phiên ngày hoàn tất để xác nhận tín hiệu")
        return frame

    def _read_disk_cache(self, symbol: str, days: int, fresh_only: bool = True) -> pd.DataFrame | None:
        path = self._disk_path(symbol)
        try:
            if not path.exists():
                return None
            file_age = time.time() - path.stat().st_mtime
            if fresh_only and file_age > self.cache_seconds:
                return None
            frame = self._completed_sessions(normalize_ohlcv(pd.read_csv(path)))
            now = datetime.now()
            # A cache created before the close should be refreshed soon after
            # 15:10, while holidays still avoid a tight retry loop.
            if (fresh_only and now.weekday() < 5 and (now.hour, now.minute) >= (15, 10)
                    and pd.Timestamp(frame.iloc[-1].time).date() < now.date() and file_age > 1800):
                return None
            # A short scan/analyse cache must never satisfy a longer backtest
            # request. Otherwise the 200-session indicator warm-up can leave
            # only a few weeks in the actual evaluation window.
            if len(frame) < days:
                return None
            result = frame.tail(days).reset_index(drop=True)
            result.attrs["source"] = "Local persistent market cache"
            return result
        except Exception:
            return None

    def _write_disk_cache(self, symbol: str, frame: pd.DataFrame) -> None:
        path = self._disk_path(symbol)
        temp = path.with_suffix(".tmp")
        try:
            combined = frame[REQUIRED].copy()
            # Preserve deeper history already downloaded. Routine 260/500-row
            # analysis calls must not truncate a 1,500-row backtest cache.
            if path.exists():
                try:
                    existing = normalize_ohlcv(pd.read_csv(path))
                    combined = normalize_ohlcv(pd.concat([existing, combined], ignore_index=True))
                except Exception:
                    pass
            combined[REQUIRED].to_csv(temp, index=False, encoding="utf-8")
            os.replace(temp, path)
        except Exception as exc:
            log.warning("Cannot persist market cache for %s: %s", symbol, type(exc).__name__)
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def _wait_for_vnstock_slot(self) -> None:
        """Serialize Vnstock Free fallbacks so concurrent SSI failures do not exceed its quota."""
        with self._fallback_lock:
            remaining = self.fallback_interval_seconds - (time.monotonic() - self._last_fallback_request)
            if remaining > 0:
                time.sleep(remaining)
            self._last_fallback_request = time.monotonic()

    def symbols_by_group(self, group: str = "VN100") -> list[str]:
        """Return a live index basket and cache it for a day."""
        group = group.strip().upper()
        with self._cache_lock:
            cached = self._group_cache.get(group)
            if cached and time.monotonic() - cached[0] < 86400:
                return cached[1].copy()
        if self.ssi and self.ssi.configured:
            try:
                symbols = self.ssi.index_components(group)
                symbols = [x for x in symbols if re.fullmatch(r"[A-Z]{3}", x)]
                if symbols:
                    with self._cache_lock:
                        self._group_cache[group] = (time.monotonic(), symbols.copy())
                    return symbols
            except Exception as exc:
                log.warning("SSI index components unavailable for %s: %s", group, type(exc).__name__)
        try:
            from vnstock.api.listing import Listing
            with contextlib.redirect_stdout(io.StringIO()):
                raw = Listing(source=self.source, random_agent=False, show_log=False).symbols_by_group(
                    group=group, show_log=False)
            values = raw.tolist() if hasattr(raw, "tolist") else list(raw)
            symbols = list(dict.fromkeys(
                str(value).strip().upper() for value in values
                if re.fullmatch(r"[A-Z]{3}", str(value).strip().upper())
            ))
            if not symbols:
                raise DataError(f"Rổ {group} không trả về mã cổ phiếu hợp lệ")
            with self._cache_lock:
                self._group_cache[group] = (time.monotonic(), symbols.copy())
            return symbols
        except Exception as exc:
            raise DataError(f"Không lấy được danh sách rổ {group}: {exc}") from exc

    @staticmethod
    def _plain(value: str) -> str:
        text = unicodedata.normalize("NFD", str(value).lower())
        return "".join(c for c in text if unicodedata.category(c) != "Mn").replace("đ", "d")

    def symbols_by_industry(self, query: str) -> tuple[str, list[str]]:
        """Resolve an ICB industry name and return its listed symbols."""
        normalized = self._plain(query).strip()
        if len(normalized) < 2:
            raise DataError("Tên ngành quá ngắn")
        with self._cache_lock:
            cached = self._industry_cache
        if cached and time.monotonic() - cached[0] < 86400:
            frame = cached[1]
        else:
            try:
                from vnstock.api.listing import Listing
                with contextlib.redirect_stdout(io.StringIO()):
                    frame = Listing(source=self.source, random_agent=False, show_log=False).symbols_by_industries()
                required = {"symbol", "icb_name", "icb_level"}
                if frame is None or frame.empty or not required.issubset(frame.columns):
                    raise DataError("Danh mục ngành không đúng định dạng")
                with self._cache_lock:
                    self._industry_cache = (time.monotonic(), frame.copy())
            except Exception as exc:
                raise DataError(f"Không lấy được danh mục ngành: {type(exc).__name__}") from exc
        work = frame.copy()
        work["_plain_industry"] = work["icb_name"].map(self._plain)
        names = work[["icb_name", "icb_level", "_plain_industry"]].drop_duplicates()
        candidates = names[names["_plain_industry"].map(
            lambda name: normalized in name or name in normalized)]
        if candidates.empty:
            raise DataError(f"Không tìm thấy ngành gần với '{query}'")
        candidates = candidates.assign(
            exact=(candidates["_plain_industry"] == normalized).astype(int),
            distance=candidates["_plain_industry"].map(lambda name: abs(len(name) - len(normalized))))
        selected = candidates.sort_values(["exact", "icb_level", "distance"],
                                          ascending=[False, False, True]).iloc[0]
        rows = work[(work["icb_name"] == selected["icb_name"]) &
                    (work["icb_level"] == selected["icb_level"])]
        symbols = list(dict.fromkeys(str(x).upper().strip() for x in rows["symbol"]
                                    if re.fullmatch(r"[A-Z]{3}", str(x).upper().strip())))
        if not symbols:
            raise DataError(f"Ngành {selected['icb_name']} chưa có mã hợp lệ")
        return str(selected["icb_name"]), symbols

    def history(self, symbol: str, days: int = 500) -> pd.DataFrame:
        symbol = symbol.strip().upper()
        cache_key = (symbol, days)
        with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached and time.monotonic() - cached[0] < 900:
                return cached[1].copy()
        disk_cached = self._read_disk_cache(symbol, days, fresh_only=True)
        if disk_cached is not None:
            with self._cache_lock:
                self._cache[cache_key] = (time.monotonic(), disk_cached.copy())
            return disk_cached
        stale_cached = self._read_disk_cache(symbol, days, fresh_only=False)
        local_fallback = None
        if symbol in {"VNINDEX", "VN-INDEX", "VNI"} and self.vnindex_csv.exists():
            local_fallback = normalize_ohlcv(pd.read_csv(self.vnindex_csv)).tail(days).reset_index(drop=True)
            age = (date.today() - pd.Timestamp(local_fallback.iloc[-1].time).date()).days
            if age <= 10 and not (self.ssi and self.ssi.configured and self.ssi_primary):
                local_fallback.attrs["source"] = "Local VNINDEX.csv"
                with self._cache_lock:
                    self._cache[cache_key] = (time.monotonic(), local_fallback.copy())
                return local_fallback
            symbol = "VNINDEX"
        if self.ssi and self.ssi.configured and self.ssi_primary:
            try:
                result = self._completed_sessions(normalize_ohlcv(
                    self.ssi.daily_ohlc(symbol, days))).tail(days).reset_index(drop=True)
                result.attrs["source"] = "SSI FastConnect Data / DailyOhlc"
                with self._cache_lock:
                    self._cache[cache_key] = (time.monotonic(), result.copy())
                self._write_disk_cache(symbol, result)
                return result
            except Exception as exc:
                log.warning("SSI daily OHLC unavailable for %s; falling back: %s", symbol, type(exc).__name__)
        if local_fallback is not None and (date.today() - pd.Timestamp(local_fallback.iloc[-1].time).date()).days <= 10:
            local_fallback.attrs["source"] = "Local VNINDEX.csv"
            with self._cache_lock:
                self._cache[cache_key] = (time.monotonic(), local_fallback.copy())
            return local_fallback
        try:
            from vnstock.api.quote import Quote

            end = date.today()
            start = end - timedelta(days=max(days * 2, 400))
            # Vnstock emits informational Unicode text to stdout; capture it so
            # Windows services using a legacy console encoding cannot crash.
            self._wait_for_vnstock_slot()
            with contextlib.redirect_stdout(io.StringIO()):
                quote = Quote(symbol=symbol, source=self.source, show_log=False)
                raw = quote.history(start=start.isoformat(), end=end.isoformat(), interval="1D")
            result = self._completed_sessions(normalize_ohlcv(raw)).tail(days).reset_index(drop=True)
            result.attrs["source"] = f"Vnstock Quote/{self.source}"
            with self._cache_lock:
                self._cache[cache_key] = (time.monotonic(), result.copy())
            self._write_disk_cache(symbol, result)
            return result
        except Exception as exc:
            if local_fallback is not None:
                return local_fallback
            if stale_cached is not None:
                stale_cached.attrs["source"] = "Local stale market cache (network fallback)"
                return stale_cached
            raise DataError(f"Không lấy được dữ liệu {symbol}: {exc}") from exc

    def intraday_snapshot(self, symbol: str) -> dict:
        if not self.ssi or not self.ssi.configured:
            raise DataError("SSI realtime chưa được cấu hình bằng ConsumerID/ConsumerSecret")
        try:
            return self.ssi.intraday_snapshot(symbol)
        except Exception as exc:
            raise DataError(str(exc)) from exc

    @staticmethod
    def assert_fresh(df: pd.DataFrame, max_calendar_days: int = 10) -> None:
        last = pd.Timestamp(df.iloc[-1]["time"]).date()
        age = (date.today() - last).days
        if age > max_calendar_days:
            raise DataError(f"Dữ liệu đã cũ {age} ngày, phiên cuối {last:%d/%m/%Y}")
