from __future__ import annotations

import base64
import json
import threading
import time
from datetime import date, timedelta

import pandas as pd
import requests


class SSIError(RuntimeError):
    pass


class SSIDataClient:
    """Read-only adapter for SSI FastConnect Data. It never calls Trading APIs."""

    def __init__(self, consumer_id: str, consumer_secret: str,
                 base_url: str = "https://fc-data.ssi.com.vn/api/v2/Market", timeout: int = 25):
        self.consumer_id = consumer_id.strip()
        self.consumer_secret = consumer_secret.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self._token = ""
        self._token_expiry = 0.0
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.consumer_id and self.consumer_secret)

    @staticmethod
    def _jwt_expiry(token: str) -> float:
        try:
            payload = token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            return float(json.loads(base64.urlsafe_b64decode(payload.encode()))["exp"])
        except (ValueError, KeyError, IndexError, json.JSONDecodeError):
            return time.time() + 45 * 60

    def access_token(self, force: bool = False) -> str:
        if not self.configured:
            raise SSIError("SSI FastConnect Data chưa có ConsumerID/ConsumerSecret")
        with self._lock:
            if not force and self._token and time.time() < self._token_expiry - 60:
                return self._token
            try:
                response = self.session.post(
                    f"{self.base_url}/AccessToken",
                    json={"consumerID": self.consumer_id, "consumerSecret": self.consumer_secret},
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                    timeout=self.timeout,
                )
                response.raise_for_status()
                body = response.json()
            except (requests.RequestException, ValueError) as exc:
                raise SSIError(f"Không lấy được SSI access token: {type(exc).__name__}") from exc
            data = body.get("data") if isinstance(body, dict) else None
            token = (data or {}).get("accessToken") if isinstance(data, dict) else None
            token = token or (body.get("accessToken") if isinstance(body, dict) else None)
            if not token:
                message = body.get("message", "phản hồi không có accessToken") if isinstance(body, dict) else "phản hồi sai định dạng"
                raise SSIError(f"SSI từ chối xác thực: {message}")
            self._token = str(token)
            self._token_expiry = self._jwt_expiry(self._token)
            return self._token

    def _get(self, endpoint: str, params: dict) -> dict:
        token = self.access_token()
        try:
            response = self.session.get(
                f"{self.base_url}/{endpoint}", params=params,
                headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
                timeout=self.timeout,
            )
            if response.status_code == 401:
                token = self.access_token(force=True)
                response = self.session.get(
                    f"{self.base_url}/{endpoint}", params=params,
                    headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
                    timeout=self.timeout,
                )
            response.raise_for_status()
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise SSIError(f"SSI {endpoint} lỗi: {type(exc).__name__}") from exc
        if not isinstance(body, dict):
            raise SSIError(f"SSI {endpoint} trả về sai định dạng")
        status = body.get("status")
        if status not in (None, 200, "200", "Success", "SUCCESS"):
            raise SSIError(f"SSI {endpoint}: {body.get('message', status)}")
        return body

    def index_components(self, index_code: str) -> list[str]:
        body = self._get("IndexComponents", {
            "indexCode": index_code.upper(), "pageIndex": 1, "pageSize": 1000})
        symbols = []
        for group in body.get("data") or []:
            for item in group.get("IndexComponent") or group.get("indexComponent") or []:
                symbol = str(item.get("StockSymbol") or item.get("stockSymbol") or "").upper().strip()
                if symbol and symbol not in symbols:
                    symbols.append(symbol)
        if not symbols:
            raise SSIError(f"SSI không trả về thành phần {index_code.upper()}")
        return symbols

    def daily_ohlc(self, symbol: str, days: int = 500) -> pd.DataFrame:
        end = date.today()
        start = end - timedelta(days=max(days * 2, 400))
        rows = []
        for page in range(1, 11):
            body = self._get("DailyOhlc", {
                "symbol": symbol.upper(), "fromDate": start.strftime("%d/%m/%Y"),
                "toDate": end.strftime("%d/%m/%Y"), "pageIndex": page,
                "pageSize": 1000, "ascending": True})
            batch = body.get("data") or []
            rows.extend(batch)
            total = int(body.get("totalRecord") or len(rows))
            if not batch or len(rows) >= total:
                break
        if not rows:
            raise SSIError(f"SSI không có DailyOhlc cho {symbol.upper()}")
        frame = pd.DataFrame(rows).rename(columns={
            "TradingDate": "time", "Open": "open", "High": "high", "Low": "low",
            "Close": "close", "Volume": "volume"})
        return frame[["time", "open", "high", "low", "close", "volume"]].tail(days)

    def intraday_snapshot(self, symbol: str) -> dict:
        today = date.today().strftime("%d/%m/%Y")
        body = self._get("IntradayOhlc", {
            "symbol": symbol.upper(), "fromDate": today, "toDate": today,
            "pageIndex": 1, "pageSize": 1000, "ascending": False, "resolution": 1})
        rows = body.get("data") or []
        if not rows:
            raise SSIError(f"SSI chưa có dữ liệu trong phiên cho {symbol.upper()}")
        def stamp(row):
            return pd.to_datetime(
                f"{row.get('TradingDate', '')} {row.get('Time', '')}", dayfirst=True, errors="coerce")
        latest = max(rows, key=lambda row: stamp(row) if not pd.isna(stamp(row)) else pd.Timestamp.min)
        return {
            "symbol": symbol.upper(), "time": stamp(latest),
            "open": float(latest.get("Open") or 0), "high": float(latest.get("High") or 0),
            "low": float(latest.get("Low") or 0), "close": float(latest.get("Close") or 0),
            "volume": float(latest.get("Volume") or 0), "value": float(latest.get("Value") or 0),
            "source": "SSI FastConnect Data / IntradayOhlc (1 phút)",
        }

    def healthcheck(self) -> dict:
        started = time.perf_counter()
        self.access_token(force=True)
        return {"ok": True, "latency_ms": round((time.perf_counter() - started) * 1000)}
