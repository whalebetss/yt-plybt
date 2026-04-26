"""Flip one or more YouTube video IDs to privacyStatus=public.

Usage:
    python set_public.py PIH-iTkbk-g zzxx1w2LBo4
"""
from __future__ import annotations

import sys
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config.settings import get_settings
from src.utils.logger import log
from src.youtube.uploader import (
    API_SERVICE_NAME,
    API_VERSION,
    YouTubeUploader,
)


def main(video_ids: list[str]) -> int:
    settings = get_settings()
    uploader = YouTubeUploader(
        client_secrets_file=Path(settings.youtube_client_secrets_file),
        token_file=Path(settings.youtube_token_file),
    )
    creds = uploader._get_credentials()
    yt = build(API_SERVICE_NAME, API_VERSION, credentials=creds)

    for vid in video_ids:
        try:
            yt.videos().update(
                part="status",
                body={
                    "id": vid,
                    "status": {
                        "privacyStatus": "public",
                        "selfDeclaredMadeForKids": settings.youtube_made_for_kids,
                    },
                },
            ).execute()
            log.info("[{}] -> public", vid)
        except HttpError as exc:
            log.error("[{}] update failed: {}", vid, exc)
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python set_public.py <video_id> [<video_id> ...]")
        sys.exit(1)
    sys.exit(main(sys.argv[1:]))
