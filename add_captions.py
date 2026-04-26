"""Translate the SRT in a run dir and upload caption tracks to a video.

Usage:
    python add_captions.py output/2026-04-25T00-28-44 wjym-4NWq8I

This is a one-shot tool for backfilling captions on videos that were
uploaded before the multi-language support landed.
"""
from __future__ import annotations

import sys
from pathlib import Path

from config.settings import get_settings
from src.utils.logger import log
from src.video.srt_translator import translate_srt_to_many
from src.youtube.uploader import YouTubeUploader


def main(run_dir_str: str, video_id: str) -> int:
    settings = get_settings()
    run_dir = Path(run_dir_str).resolve()
    src_srt = run_dir / "subtitles.srt"

    if not src_srt.exists():
        log.error("subtitles.srt missing in {}", run_dir)
        return 2

    langs = [c.strip() for c in (settings.caption_languages or "").split(",") if c.strip()]
    if not langs:
        log.error("CAPTION_LANGUAGES is empty in .env")
        return 2

    source_lang = langs[0]
    log.info("Translating {} into {} (source={})", src_srt.name, langs, source_lang)
    paths = translate_srt_to_many(src_srt, langs, source_lang=source_lang)
    log.info("Got {} caption files: {}", len(paths), [p.name for p in paths.values()])

    uploader = YouTubeUploader(
        client_secrets_file=Path(settings.youtube_client_secrets_file),
        token_file=Path(settings.youtube_token_file),
    )
    log.info("Uploading captions to video https://youtu.be/{}", video_id)
    results = uploader.upload_captions(
        video_id=video_id,
        srt_paths=paths,
        source_lang=source_lang,
    )

    if not results:
        log.error("No caption tracks were accepted by YouTube.")
        return 1

    log.info("DONE - {} tracks attached: {}", len(results), list(results.keys()))
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python add_captions.py <run_dir> <video_id>")
        sys.exit(1)
    sys.exit(main(sys.argv[1], sys.argv[2]))
