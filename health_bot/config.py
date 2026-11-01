from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from os import environ
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import dotenv_values


class ConfigError(ValueError):
    """Raised when required or unsafe configuration is supplied."""


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_user_id: int
    telegram_chat_id: int
    timezone: ZoneInfo
    database_path: Path = Path("health_bot.sqlite3")
    cache_ttl_seconds: int = 3600
    report_hour: int = 12
    report_minute: int = 0
    garmin_email: str = ""
    garmin_password: str = ""
    garmin_session_dir: str = "~/.garminconnect"
    fatsecret_consumer_key: str = ""
    fatsecret_consumer_secret: str = ""
    fatsecret_oauth_token: str = ""
    fatsecret_oauth_token_secret: str = ""

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> Settings:
        def required(name: str) -> str:
            value = str(values.get(name, "")).strip()
            if not value:
                raise ConfigError(f"{name} is required")
            return value

        try:
            user_id = int(required("TELEGRAM_USER_ID"))
            chat_id = int(required("TELEGRAM_CHAT_ID"))
            ttl = int(str(values.get("CACHE_TTL_SECONDS", 3600)))
            hour = int(str(values.get("REPORT_HOUR", 12)))
            minute = int(str(values.get("REPORT_MINUTE", 0)))
        except ValueError as exc:
            raise ConfigError("IDs, cache TTL and report time must be integers") from exc
        if not 1 <= ttl <= 86400:
            raise ConfigError("CACHE_TTL_SECONDS must be between 1 and 86400")
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ConfigError("Invalid report time")
        timezone_name = str(values.get("TIMEZONE", "UTC"))
        try:
            tz = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ConfigError(f"Unknown IANA timezone: {timezone_name}") from exc
        return cls(
            telegram_bot_token=required("TELEGRAM_BOT_TOKEN"),
            telegram_user_id=user_id,
            telegram_chat_id=chat_id,
            timezone=tz,
            database_path=Path(str(values.get("DATABASE_PATH", "health_bot.sqlite3"))),
            cache_ttl_seconds=ttl,
            report_hour=hour,
            report_minute=minute,
            garmin_email=str(values.get("EMAIL", "")),
            garmin_password=str(values.get("PASSWORD", "")),
            garmin_session_dir=str(values.get("GARMINTOKENS", "~/.garminconnect")),
            fatsecret_consumer_key=str(values.get("FATSECRET_CONSUMER_KEY", "")),
            fatsecret_consumer_secret=str(values.get("FATSECRET_CONSUMER_SECRET", "")),
            fatsecret_oauth_token=str(values.get("FATSECRET_OAUTH_TOKEN", "")),
            fatsecret_oauth_token_secret=str(values.get("FATSECRET_OAUTH_TOKEN_SECRET", "")),
        )

    @classmethod
    def load(cls, env_file: Path | None = None) -> Settings:
        values: dict[str, object] = {}
        if env_file is not None:
            values.update({k: v for k, v in dotenv_values(env_file).items() if v is not None})
        values.update(environ)
        return cls.from_mapping(values)
