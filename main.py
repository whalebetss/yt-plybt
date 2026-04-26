"""One-shot pipeline entrypoint.

Usage:
    python main.py --once          # run pipeline immediately and exit
    python main.py --dry-run       # force DRY_RUN regardless of .env
"""
from __future__ import annotations

import argparse
import sys

from config.settings import get_settings
from src.pipeline import run_pipeline
from src.utils.logger import configure_logger, log


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Crypto Wallet Shorts pipeline")
    p.add_argument("--once", action="store_true", help="Run the pipeline once and exit")
    p.add_argument("--dry-run", action="store_true", help="Force DRY_RUN")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    settings = get_settings()
    if args.dry_run:
        settings.dry_run = True

    configure_logger(settings.log_level)
    log.info("Starting pipeline (dry_run={})", settings.dry_run)

    try:
        result = run_pipeline(settings)
    except Exception as exc:  # noqa: BLE001 — top-level guard
        log.exception("Pipeline failed: {}", exc)
        return 1

    if result is None:
        log.warning("Pipeline produced no output (no qualifying wallets).")
        return 0

    log.info("Pipeline finished. Run dir: {}", result.run_dir)
    if result.youtube_video_id:
        log.info("Uploaded as https://youtu.be/{}", result.youtube_video_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
