"""APScheduler loop. Runs the pipeline on the cron in RUN_SCHEDULE_CRON."""
from __future__ import annotations

import signal
import sys
import time

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from config.settings import get_settings
from src.pipeline import run_pipeline
from src.utils.logger import configure_logger, log


def _job() -> None:
    settings = get_settings()
    try:
        run_pipeline(settings)
    except Exception as exc:  # noqa: BLE001
        log.exception("Scheduled pipeline run failed: {}", exc)


def main() -> int:
    settings = get_settings()
    configure_logger(settings.log_level)

    trigger = CronTrigger.from_crontab(settings.run_schedule_cron)
    scheduler = BackgroundScheduler()
    scheduler.add_job(_job, trigger=trigger, id="pipeline", max_instances=1, coalesce=True)
    scheduler.start()
    log.info("Scheduler started with cron='{}'", settings.run_schedule_cron)

    stop = {"flag": False}

    def _shutdown(*_: object) -> None:
        stop["flag"] = True

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        while not stop["flag"]:
            time.sleep(1)
    finally:
        scheduler.shutdown(wait=False)
        log.info("Scheduler stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
