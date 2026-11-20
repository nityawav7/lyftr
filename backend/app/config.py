"""Runtime configuration, read exclusively from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful, concise assistant. Answer clearly and accurately. If you are unsure about something, say so."
)


def _env(name: str, default: str) -> str:
    """Read a variable, trimming whitespace and stray CR from Windows-edited .env files."""
    raw = os.getenv(name)
    return default if raw is None else raw.strip()


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _optional_float(name: str) -> float | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    return float(raw)


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def sqlite_path_from_url(url: str) -> str:
    """Turn a SQLAlchemy-style SQLite URL into a filesystem path.

    sqlite:////data/app.db  -> /data/app.db   (absolute)
    sqlite:///./data/app.db -> ./data/app.db  (relative)
    sqlite:///:memory:      -> :memory:
    """
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise ValueError(f"DATABASE_URL must start with {prefix!r}, got {url!r}")
    path = url[len(prefix) :]
    if not path:
        raise ValueError("DATABASE_URL is missing a file path")
    return path


@dataclass(frozen=True)
class Settings:
    api_keys: list[str]
    default_model: str
    key_models: list[str]
    database_path: str
    allowed_origins: list[str]
    enable_failure_simulation: bool
    default_system_prompt: str
    cache_enabled: bool
    cache_type: str
    cache_dir: str
    cache_ttl_seconds: int
    llm_timeout_seconds: float
    llm_temperature: float | None
    llm_max_output_tokens: int
    history_max_messages: int
    rate_limit_cooldown_seconds: int
    auth_error_cooldown_seconds: int
    log_level: str = "INFO"

    @property
    def model_for_key(self) -> list[str]:
        """Model name per key, aligned with api_keys (key_1 -> index 0)."""
        return [
            self.key_models[i] if i < len(self.key_models) and self.key_models[i] else self.default_model
            for i in range(len(self.api_keys))
        ]

    @classmethod
    def from_env(cls) -> Settings:
        database_url = _env("DATABASE_URL", "sqlite:///./data/app.db")
        database_path = sqlite_path_from_url(database_url)
        default_cache_dir = (
            str(Path(database_path).parent / "llm_cache") if database_path != ":memory:" else "./data/llm_cache"
        )
        return cls(
            api_keys=_csv(_env("LLM_API_KEYS", "")),
            default_model=_env("LLM_MODEL", "gemini/gemini-3.5-flash-lite"),
            key_models=_csv(_env("LLM_KEY_MODELS", "")),
            database_path=database_path,
            allowed_origins=_csv(_env("ALLOWED_ORIGINS", "http://localhost:3000")),
            enable_failure_simulation=_bool("ENABLE_FAILURE_SIMULATION", False),
            default_system_prompt=_env("DEFAULT_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT) or DEFAULT_SYSTEM_PROMPT,
            cache_enabled=_bool("CACHE_ENABLED", True),
            cache_type=_env("CACHE_TYPE", "disk").lower(),
            cache_dir=_env("CACHE_DIR", default_cache_dir) or default_cache_dir,
            cache_ttl_seconds=_int("CACHE_TTL_SECONDS", 7 * 24 * 3600),
            llm_timeout_seconds=_float("LLM_TIMEOUT_SECONDS", 20.0),
            # Unset by default: Gemini 3+ deprecates sampling params in favour of instructions.
            llm_temperature=_optional_float("LLM_TEMPERATURE"),
            llm_max_output_tokens=_int("LLM_MAX_OUTPUT_TOKENS", 1024),
            history_max_messages=_int("HISTORY_MAX_MESSAGES", 40),
            rate_limit_cooldown_seconds=_int("KEY_COOLDOWN_SECONDS", 30),
            auth_error_cooldown_seconds=_int("KEY_AUTH_COOLDOWN_SECONDS", 600),
            log_level=(_env("LOG_LEVEL", "INFO") or "INFO").upper(),
        )
