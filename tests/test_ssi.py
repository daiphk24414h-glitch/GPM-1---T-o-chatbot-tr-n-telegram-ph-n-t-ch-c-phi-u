from unittest.mock import Mock

import pandas as pd

from app.ssi import SSIDataClient
from app.config import Settings


def response(body, status=200):
    item = Mock()
    item.status_code = status
    item.raise_for_status.return_value = None
    item.json.return_value = body
    return item


def client_with_token():
    client = SSIDataClient("consumer", "secret")
    client.session.post = Mock(return_value=response({"status": 200, "data": {"accessToken": "opaque-token"}}))
    return client


def test_ssi_token_is_cached_without_exposing_credentials():
    client = client_with_token()
    assert client.access_token() == "opaque-token"
    assert client.access_token() == "opaque-token"
    assert client.session.post.call_count == 1


def test_ssi_index_components_are_normalized():
    client = client_with_token()
    client.session.get = Mock(return_value=response({
        "status": "Success", "data": [{"IndexComponent": [
            {"StockSymbol": "FPT"}, {"StockSymbol": "VCB"}, {"StockSymbol": "FPT"}]}]}))
    assert client.index_components("vn100") == ["FPT", "VCB"]


def test_ssi_intraday_returns_latest_bar():
    client = client_with_token()
    client.session.get = Mock(return_value=response({"status": "Success", "data": [
        {"TradingDate": "22/09/2026", "Time": "09:31:00", "Open": "100", "High": "102",
         "Low": "99", "Close": "101", "Volume": "1000", "Value": "101000"},
        {"TradingDate": "22/09/2026", "Time": "09:32:00", "Open": "101", "High": "103",
         "Low": "100", "Close": "102", "Volume": "1200", "Value": "122400"}]}))
    quote = client.intraday_snapshot("fpt")
    assert quote["close"] == 102
    assert quote["time"] == pd.Timestamp("2026-09-22 09:32:00")


def test_settings_accept_user_ssi_variable_names(tmp_path, monkeypatch):
    for name in ("SSI_CONSUMER_ID", "CONSUMERID_SSI", "SSI_CONSUMER_SECRET",
                 "CONSUMERSECRET_SSI", "SSI_PUBLIC_KEY", "PUBLICKEY_SSI", "SSI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / ".env"
    env.write_text("CONSUMERID_SSI=id\nCONSUMERSECRET_SSI=secret\nPUBLICKEY_SSI=public\nSSI_API_KEY=key\n")
    settings = Settings(_env_file=env)
    assert settings.ssi_configured
    assert settings.ssi_consumer_id == "id"
    assert settings.ssi_public_key == "public"
