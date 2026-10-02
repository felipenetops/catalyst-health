"""Leitura de host, credenciais e opções a partir do ambiente."""

from __future__ import annotations

import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
except ImportError:  # o fluxo interativo não depende de arquivo .env
    def load_dotenv(env_file: str | None = None) -> bool:
        return False


_FALSE_VALUES = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class Settings:
    host: str
    username: str
    password: str
    verify_ssl: bool = True
    timeout: float = 30.0

    def missing_fields(self) -> list[str]:
        missing: list[str] = []
        if not self.host:
            missing.append("CATALYST_CENTER_HOST")
        if not self.username:
            missing.append("CATALYST_CENTER_USERNAME")
        if not self.password:
            missing.append("CATALYST_CENTER_PASSWORD")
        return missing


def _as_bool(value: str | None, default: bool = True) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() not in _FALSE_VALUES


def load_settings(
    *,
    host: str | None = None,
    username: str | None = None,
    password: str | None = None,
    verify_ssl: bool | None = None,
    timeout: float | None = None,
    env_file: str | None = None,
) -> Settings:
    """Monta as configurações. Argumentos explícitos têm prioridade sobre o ambiente."""
    load_dotenv(env_file)

    timeout_raw = os.environ.get("CATALYST_CENTER_TIMEOUT", "30")
    try:
        env_timeout = float(timeout_raw)
    except ValueError as exc:
        raise ValueError(
            f"CATALYST_CENTER_TIMEOUT inválido: {timeout_raw!r}"
        ) from exc

    return Settings(
        host=(host if host is not None else os.environ.get("CATALYST_CENTER_HOST", "")).strip(),
        username=(
            username if username is not None else os.environ.get("CATALYST_CENTER_USERNAME", "")
        ).strip(),
        password=password if password is not None else os.environ.get("CATALYST_CENTER_PASSWORD", ""),
        verify_ssl=(
            verify_ssl
            if verify_ssl is not None
            else _as_bool(os.environ.get("CATALYST_CENTER_VERIFY_SSL"), default=True)
        ),
        timeout=timeout if timeout is not None else env_timeout,
    )
