import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from telegram.error import NetworkError

from app.bot import TelegramBot
from app.models import Signal


def test_transient_telegram_progress_failure_is_retried_without_aborting():
    bot = TelegramBot.__new__(TelegramBot)
    message = Mock()
    message.edit_text = AsyncMock(side_effect=[NetworkError("temporary"), None])
    with patch("app.bot.asyncio.sleep", new=AsyncMock()):
        succeeded = asyncio.run(bot._safe_edit(message, "50/100"))
    assert succeeded
    assert message.edit_text.await_count == 2


def test_transient_telegram_delivery_failure_is_retried_without_losing_result():
    bot = TelegramBot.__new__(TelegramBot)
    operation = AsyncMock(side_effect=[NetworkError("temporary"), {"message_id": 1}])
    with patch("app.bot.asyncio.sleep", new=AsyncMock()):
        delivered = asyncio.run(bot._telegram_call(operation, "send result"))
    assert delivered == {"message_id": 1}
    assert operation.await_count == 2


def test_entrypoint_imports_after_pid_lifecycle_changes():
    import main
    assert callable(main.main)
    assert callable(main.process_id_file)


def test_incomplete_listing_is_skipped_instead_of_aborting_entire_screen():
    incomplete = SimpleNamespace(signal=Signal.BLOCKED, components={})
    missing_trend = SimpleNamespace(signal=Signal.HOLD, components={
        "relative_strength": 50, "entry_quality": 50, "volume": 50, "risk_liquidity": 50})
    complete = SimpleNamespace(signal=Signal.HOLD, components={
        "relative_strength": 50, "trend": 50, "entry_quality": 50,
        "volume": 50, "risk_liquidity": 50})
    assert not TelegramBot._rankable_technical(incomplete)
    assert not TelegramBot._rankable_technical(missing_trend)
    assert TelegramBot._rankable_technical(complete)
