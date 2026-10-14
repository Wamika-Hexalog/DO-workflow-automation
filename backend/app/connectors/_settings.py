"""The settings the copied connector reads as ``settings``.

create_app calls use() with its own, so the connector and the app never disagree. The
connector files keep python-brain's code, which reads one module-level ``settings``.
"""
from __future__ import annotations

from typing import Any

from ..config import Settings

_current: Settings | None = None


def use(settings: Settings) -> None:
    global _current
    _current = settings


def current() -> Settings:
    global _current
    if _current is None:
        _current = Settings.from_env()
    return _current


class _SettingsProxy:
    def __getattr__(self, name: str) -> Any:
        return getattr(current(), name)


settings = _SettingsProxy()
