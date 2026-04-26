"""Replace a uploaded video with a clean re-render + re-attach captions.

YouTube doesn't let you swap the underlying video file on an existing
upload, so the only way to "replace" is delete + re-upload (with a fresh
URL). This script does:
    1. videos.delete on the old id
    2. videos.insert with the new mp4 + same metadata.json
    3. captions.insert for each language SRT in the run dir

Usage:
    python replace_video.py output/2026-04-25T00-28-44 wjym-4NWq8I
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config.settings import get_settings
from src.utils.logger import log
from src.video.srt_translator import translate_srt_to_many
from src.youtube.uploader import (
    API_SERVICE_NAME,
    API_VERSION,
    YouTubeUploader,
)


def main(run_dir_str: str, old_video_id: str) -> int:
    settings = get_settings()
    run_dir = Path(run_dir_str).resolve()
    video_path = run_dir / "video.mp4"
    metadata_path = run_dir / "metadata.json"
    src_srt = run_dir / "subtitles.srt"

    for must_exist in (video_path, metadata_path, src_srt):
        if not must_exist.exists():
            log.error("missing: {}", must_exist)
            return 2

    meta = json.loads(metadata_path.read_text(encoding="utf-8"))
    langs = [c.strip() for c in (settings.caption_languages or "").split(",") if c.strip()]

    uploader = YouTubeUploader(
        client_secrets_file=Path(settings.youtube_client_secrets_file),
        token_file=Path(settings.youtube_token_file),
    )

    # Touch the API once to materialize the OAuth client + token refresh.
    creds = uploader._get_credentials()
    yt = build(API_SERVICE_NAME, API_VERSION, credentials=creds)
    uploader._client = yt

    # 1) Delete the stale upload.
    try:
        yt.videos().delete(id=old_video_id).execute()
        log.info("Deleted old video id={}", old_video_id)
    except HttpError as exc:
        log.warning("Could not delete {}: {}", old_video_id, exc)

    # 2) Upload the clean re-render.
    new_id = uploader.upload(
        video_path=video_path,
        title=meta["title"],
        description=meta["description"],
        tags=meta.get("tags", []),
        category_id=meta.get("category_id", settings.youtube_category_id),
        privacy_status=settings.youtube_privacy_status,
        made_for_kids=settings.youtube_made_for_kids,
    )

    # 3) Translate and attach all caption tracks.
    log.info("Translating SRT to {}...", langs)
    paths = translate_srt_to_many(src_srt, langs, source_lang=langs[0] if langs else "en")
    uploader.upload_captions(
        video_id=new_id,
        srt_paths=paths,
        source_lang=langs[0] if langs else "en",
    )

    log.info("DONE - replacement video: https://youtu.be/{}", new_id)
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python replace_video.py <run_dir> <old_video_id>")
        sys.exit(1)
    sys.exit(main(sys.argv[1], sys.argv[2]))
