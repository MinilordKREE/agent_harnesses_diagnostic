"""Secrets, loaded only here, only from the environment or ``.env``.

No reference source: written fresh for ahd (see docs/reuse/M0.md). The masking rule in
:func:`mask_secret` follows agentic-harness-engineering ``evolve.py`` lines 48-54 (MIT) as a
one-line rule, not copied code.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from ahd.errors import ConfigError


class Settings(BaseSettings):
    """Secrets and nothing else. Non-secret knobs live in the run config."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    deepseek_api_key: SecretStr
    hf_token: SecretStr | None = None
    serper_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "SERPER_API_KEY", "serper_api_key", "Serper_key", "serper_key"
        ),
    )
    """Serper web-search key. Evo-Bench expects it as ``SERPER_API_KEY``; the owner's ``.env``
    spells it ``Serper_key``, so both names are accepted."""


def load_settings(env_file: Path | None = Path(".env")) -> Settings:
    """Load secrets from ``env_file`` (if it exists) and the process environment.

    Raises :class:`ConfigError` naming the missing variable; never echoes values. The env
    file is bound through a subclass config because pydantic's dataclass-transform hides the
    ``_env_file`` keyword from mypy.
    """

    class _BoundSettings(Settings):
        model_config = SettingsConfigDict(
            env_file=env_file,
            env_file_encoding="utf-8",
            extra="ignore",
            case_sensitive=False,
            populate_by_name=True,
        )

    try:
        return _BoundSettings()
    except ValidationError as exc:
        missing = ", ".join(str(err["loc"][0]).upper() for err in exc.errors())
        raise ConfigError(
            f"missing or invalid secret(s): {missing}. Copy .env.example to .env and fill them in."
        ) from exc


def mask_secret(value: str) -> str:
    """Return a display-safe form of a secret: first and last four characters only."""
    if len(value) > 10:
        return f"{value[:4]}***{value[-4:]}"
    return "***"
