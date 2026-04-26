"""Loguru wrapper. Import `log` everywhere."""
from __future__ import annotations

import sys

from loguru import logger as log

_configured = False


def configure_logger(level: str = "INFO") -> None:
    global _configured
    if _configured:
        return
    log.remove()
    log.add(
        sys.stderr,
        level=level.upper(),
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
    )
    _configured = True


__all__ = ["log", "configure_logger"]
