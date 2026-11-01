from __future__ import annotations

import logging
import traceback
from dataclasses import replace
from pathlib import Path

from fatsecret import Fatsecret
from garminconnect import Garmin
from telegram import Update

from .bot import HealthBotService, build_application
from .clients import FatSecretAdapter, GarminAdapter, SourceUnavailableError
from .config import ConfigError, Settings
from .storage import SQLiteStorage

logger = logging.getLogger(__name__)

_redaction_tokens: set[str] = set()
_record_factory_installed = False


def _redact(value, token: str):
    if isinstance(value, str):
        return value.replace(token, "[REDACTED]")
    if isinstance(value, tuple):
        return tuple(_redact(item, token) for item in value)
    if isinstance(value, dict):
        return {_redact(key, token): _redact(item, token) for key, item in value.items()}
    return value


class TokenRedactionFilter(logging.Filter):
    """Remove the exact bot token before any handler formats a record."""

    def __init__(self, token: str):
        super().__init__()
        self.token = token

    def filter(self, record: logging.LogRecord) -> bool:
        if not self.token:
            return True
        record.msg = _redact(record.msg, self.token)
        record.args = _redact(record.args, self.token)
        if record.stack_info:
            record.stack_info = record.stack_info.replace(self.token, "[REDACTED]")
        if record.exc_info:
            rendered = "".join(traceback.format_exception(*record.exc_info))
            record.exc_text = rendered.replace(self.token, "[REDACTED]")
        elif record.exc_text:
            record.exc_text = record.exc_text.replace(self.token, "[REDACTED]")
        return True


def configure_logging(telegram_bot_token: str) -> None:
    global _record_factory_installed
    _redaction_tokens.add(telegram_bot_token)
    if not _record_factory_installed:
        original_factory = logging.getLogRecordFactory()

        def redacting_factory(*args, **kwargs):
            record = original_factory(*args, **kwargs)
            for token in _redaction_tokens:
                TokenRedactionFilter(token).filter(record)
            return record

        logging.setLogRecordFactory(redacting_factory)
        _record_factory_installed = True

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for handler in logging.getLogger().handlers:
        handler.addFilter(TokenRedactionFilter(telegram_bot_token))
    for name in ("httpx", "httpcore", "telegram.request", "telegram.ext.ExtBot"):
        logging.getLogger(name).setLevel(logging.WARNING)


class UnavailableClient:
    def __init__(self, source: str):
        self.source = source

    def __getattr__(self, name: str):
        raise SourceUnavailableError(self.source)


def create_application(settings: Settings):
    storage = SQLiteStorage(settings.database_path)
    storage.initialize()
    storage.set_allowed_identifiers(settings.telegram_user_id, settings.telegram_chat_id)
    saved_time = storage.get_setting("report_time")
    if saved_time is None:
        saved_time = f"{settings.report_hour:02d}:{settings.report_minute:02d}"
        storage.set_setting("report_time", saved_time)
    saved_hour, saved_minute = (int(part) for part in saved_time.split(":", 1))
    settings = replace(settings, report_hour=saved_hour, report_minute=saved_minute)
    if storage.get_setting("timezone") is None:
        storage.set_setting("timezone", settings.timezone.key)

    fat_client = UnavailableClient("FatSecret")
    if settings.fatsecret_consumer_key and settings.fatsecret_consumer_secret:
        session = None
        if settings.fatsecret_oauth_token and settings.fatsecret_oauth_token_secret:
            session = (settings.fatsecret_oauth_token, settings.fatsecret_oauth_token_secret)
        if session:
            fat_client = Fatsecret(
                settings.fatsecret_consumer_key,
                settings.fatsecret_consumer_secret,
                session_token=session,
            )
        else:
            fat_client = Fatsecret(settings.fatsecret_consumer_key, settings.fatsecret_consumer_secret)

    garmin_client = UnavailableClient("Garmin")
    if settings.garmin_email and settings.garmin_password:
        try:
            garmin_client = Garmin(settings.garmin_email, settings.garmin_password)
            garmin_client.login(settings.garmin_session_dir)
        except Exception as exc:
            logger.warning("Garmin initialization failed error_type=%s", type(exc).__name__)
            garmin_client = UnavailableClient("Garmin")

    service = HealthBotService(
        FatSecretAdapter(fat_client, storage, settings.cache_ttl_seconds),
        GarminAdapter(garmin_client),
        storage,
    )
    return build_application(settings, service)


def main() -> None:
    try:
        settings = Settings.load(Path(__file__).resolve().parent.parent / ".env")
        configure_logging(settings.telegram_bot_token)
        application = create_application(settings)
    except ConfigError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
