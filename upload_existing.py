"""Upload an already-assembled video using its metadata.json next to it.

Usage:
    python upload_existing.py output/2026-04-25T00-28-44
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from config.settings import get_settings
from src.utils.logger import log
from src.youtube.uploader import YouTubeUploader


def main(run_dir_str: str) -> int:
    settings = get_settings()
    run_dir = Path(run_dir_str).resolve()
    video_path = run_dir / "video.mp4"
    metadata_path = run_dir / "metadata.json"

    if not video_path.exists():
        log.error("video.mp4 missing in {}", run_dir)
        return 2
    if not metadata_path.exists():
        log.error("metadata.json missing in {}", run_dir)
        return 2

    meta = json.loads(metadata_path.read_text(encoding="utf-8"))
    log.info("Uploading {} ({} bytes)", video_path, video_path.stat().st_size)
    log.info("Title: {}", meta["title"])

    uploader = YouTubeUploader(
        client_secrets_file=Path(settings.youtube_client_secrets_file),
        token_file=Path(settings.youtube_token_file),
    )

    video_id = uploader.upload(
        video_path=video_path,
        title=meta["title"],
        description=meta["description"],
        tags=meta.get("tags", []),
        category_id=meta.get("category_id", settings.youtube_category_id),
        privacy_status=settings.youtube_privacy_status,
        made_for_kids=settings.youtube_made_for_kids,
    )

    log.info("DONE -> https://youtu.be/{}", video_id)
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python upload_existing.py <run_dir>")
        sys.exit(1)
    sys.exit(main(sys.argv[1]))
