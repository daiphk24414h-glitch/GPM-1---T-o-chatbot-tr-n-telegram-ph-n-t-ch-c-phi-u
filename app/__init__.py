"""Vietnamese stock analysis Telegram bot."""

import os
from pathlib import Path

_runtime = Path(__file__).resolve().parents[1] / "runtime" / "matplotlib"
_runtime.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(_runtime))
