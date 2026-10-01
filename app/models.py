from dataclasses import asdict, dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any


class Signal(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    BLOCKED = "BLOCKED"


class Regime(StrEnum):
    UP = "UP"
    DOWN = "DOWN"
    SIDEWAY = "SIDEWAY"
    UNKNOWN = "UNKNOWN"


@dataclass(slots=True)
class TechnicalResult:
    symbol: str
    as_of: date
    signal: Signal
    regime: Regime
    strategy: str
    score: float
    price: float
    stop_loss: float | None
    take_profit: float | None
    recommended_shares: int
    reasons: list[str] = field(default_factory=list)
    indicators: dict[str, float] = field(default_factory=dict)
    components: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FundamentalResult:
    symbol: str
    as_of: date | None
    score: float | None
    status: str
    components: dict[str, float | None] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    source: str = ""
    metrics: dict[str, float | str | None] = field(default_factory=dict)

    @property
    def passes(self) -> bool:
        return self.score is not None and self.score >= 40
