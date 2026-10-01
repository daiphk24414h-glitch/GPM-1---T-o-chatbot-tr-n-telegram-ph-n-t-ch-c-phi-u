from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    telegram_bot_token: str = ""
    admin_telegram_id: int = 0
    vnstock_api_key: str = ""
    ssi_consumer_id: str = Field("", validation_alias=AliasChoices("SSI_CONSUMER_ID", "CONSUMERID_SSI"))
    ssi_consumer_secret: str = Field("", validation_alias=AliasChoices("SSI_CONSUMER_SECRET", "CONSUMERSECRET_SSI"))
    ssi_api_key: str = Field("", validation_alias=AliasChoices("SSI_API_KEY", "APIKEY_SSI"))
    ssi_public_key: str = Field("", validation_alias=AliasChoices("SSI_PUBLIC_KEY", "PUBLICKEY_SSI"))
    ssi_data_base_url: str = "https://fc-data.ssi.com.vn/api/v2/Market"
    ssi_primary_market_data: bool = True
    ai_provider: str = "openai"
    openai_api_key: str = ""
    openai_model: str = "gpt-5-mini"
    enable_web_research: bool = True
    research_max_sources: int = 5
    allow_group_users: bool = True
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"
    database_path: Path = ROOT / "runtime" / "bot.db"
    vnindex_csv: Path = ROOT / "data" / "VNINDEX.csv"
    data_source: str = "VCI"
    watchlist: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["FPT", "HPG", "SSI"])
    screen_universe: str = "VN100"
    screen_fundamental_limit: int = 10
    screen_request_interval_seconds: float = 3.2
    screen_ssi_concurrency: int = 5
    market_cache_hours: int = 12
    screen_cache_minutes: int = 30
    telegram_progress_seconds: int = 12
    position_monitor_minutes: int = 5
    log_level: str = "INFO"

    @field_validator("watchlist", mode="before")
    @classmethod
    def parse_watchlist(cls, value):
        if isinstance(value, str):
            return [x.strip().upper() for x in value.split(",") if x.strip()]
        return value

    def validate_runtime(self) -> None:
        missing = []
        if not self.telegram_bot_token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if self.admin_telegram_id <= 0:
            missing.append("ADMIN_TELEGRAM_ID")
        if missing:
            raise RuntimeError("Thiếu cấu hình bắt buộc: " + ", ".join(missing))

    @property
    def ssi_configured(self) -> bool:
        return bool(self.ssi_consumer_id and self.ssi_consumer_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
