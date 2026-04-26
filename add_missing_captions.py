"""Attach the caption tracks that failed (e.g. quota-exceeded) on a video.

Looks at the existing tracks via captions.list, figures out which langs from
CAPTION_LANGUAGES are missing, and uploads only those — saves quota.

Usage:
    python add_missing_captions.py output/2026-04-25T14-11-43 PIH-iTkbk-g
"""
from __future__ import annotations

import sys
from pathlib import Path

from googleapiclient.discovery import build

from config.settings import get_settings
from src.utils.logger import log
from src.youtube.uploader import (
    API_SERVICE_NAME,
    API_VERSION,
    YouTubeUploader,
)


def main(run_dir_str: str, video_id: str) -> int:
    settings = get_settings()
    run_dir = Path(run_dir_str).resolve()

    langs = [c.strip() for c in (settings.caption_languages or "").split(",") if c.strip()]
    source_lang = langs[0] if langs else "en"

    uploader = YouTubeUploader(
        client_secrets_file=Path(settings.youtube_client_secrets_file),
        token_file=Path(settings.youtube_token_file),
    )
    creds = uploader._get_credentials()
    yt = build(API_SERVICE_NAME, API_VERSION, credentials=creds)
    uploader._client = yt

    existing = yt.captions().list(part="snippet", videoId=video_id).execute()
    existing_langs = {item["snippet"]["language"] for item in existing.get("items", [])}
    log.info("Existing caption langs on {}: {}", video_id, sorted(existing_langs))

    missing: dict[str, Path] = {}
    for lang in langs:
        if lang in existing_langs:
            continue
        srt = run_dir / ("subtitles.srt" if lang == source_lang
                          else f"subtitles.{lang}.srt")
        if srt.exists():
            missing[lang] = srt
        else:
            log.warning("No SRT on disk for {} ({})", lang, srt.name)

    if not missing:
        log.info("Nothing to do — all configured languages already attached.")
        return 0

    log.info("Uploading {} missing tracks: {}", len(missing), list(missing))
    uploader.upload_captions(
        video_id=video_id,
        srt_paths=missing,
        source_lang=source_lang,
    )
    log.info("DONE - https://youtu.be/{}", video_id)
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python add_missing_captions.py <run_dir> <video_id>")
        sys.exit(1)
    sys.exit(main(sys.argv[1], sys.argv[2]))
