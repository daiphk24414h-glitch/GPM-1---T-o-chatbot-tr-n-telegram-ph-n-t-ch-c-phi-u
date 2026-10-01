import logging
import os
from contextlib import contextmanager
from pathlib import Path

from app.bot import TelegramBot
from app.config import get_settings


@contextmanager
def single_instance(lock_path: Path):
    """Hold an OS lock for the lifetime of this polling process."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        try:
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except (ImportError, OSError) as exc:
            handle.close()
            raise RuntimeError(
                "Bot đã chạy ở một cửa sổ khác. Hãy dừng phiên cũ bằng Ctrl+C trước khi chạy lại."
            ) from exc
        yield
    finally:
        if not handle.closed:
            handle.seek(0)
            try:
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            handle.close()


@contextmanager
def process_id_file(pid_path: Path):
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(os.getpid()), encoding="ascii")
    try:
        yield
    finally:
        try:
            if pid_path.exists() and pid_path.read_text(encoding="ascii").strip() == str(os.getpid()):
                pid_path.unlink()
        except OSError:
            pass


def main():
    settings = get_settings()
    settings.validate_runtime()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # httpx logs full Telegram request URLs, and those URLs contain the bot
    # token. Never persist them in the supervisor log.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger(__name__).info("Starting VNStock Telegram Bot Quant V6.3.2")
    if settings.vnstock_api_key:
        from vnstock.core import setup_api_key
        setup_api_key(settings.vnstock_api_key)
        logging.getLogger(__name__).info("Vnstock API key activated (value hidden)")
    else:
        logging.getLogger(__name__).warning("VNSTOCK_API_KEY is empty; Guest rate limits will apply")
    with single_instance(settings.database_path.parent / "telegram-bot.lock"):
        with process_id_file(settings.database_path.parent / "bot.pid"):
            TelegramBot(settings).application().run_polling(
                drop_pending_updates=True, bootstrap_retries=-1)


if __name__ == "__main__":
    main()
