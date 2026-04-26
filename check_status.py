"""Quick: list our recent videos + their privacy + caption tracks."""
from __future__ import annotations

import sys
from pathlib import Path

from googleapiclient.discovery import build

from config.settings import get_settings
from src.youtube.uploader import API_SERVICE_NAME, API_VERSION, YouTubeUploader


def main(video_ids: list[str]) -> int:
    settings = get_settings()
    uploader = YouTubeUploader(
        client_secrets_file=Path(settings.youtube_client_secrets_file),
        token_file=Path(settings.youtube_token_file),
    )
    creds = uploader._get_credentials()
    yt = build(API_SERVICE_NAME, API_VERSION, credentials=creds)

    for vid in video_ids:
        print(f"\n=== {vid} ===")
        try:
            v = yt.videos().list(part="status,snippet", id=vid).execute()
            items = v.get("items", [])
            if not items:
                print(f"  NOT FOUND on channel")
                continue
            it = items[0]
            title = it['snippet']['title'][:80].encode('ascii', 'replace').decode('ascii')
            print(f"  Title:        {title}")
            print(f"  Privacy:      {it['status']['privacyStatus']}")
            print(f"  Published:    {it['snippet']['publishedAt']}")
        except Exception as exc:
            print(f"  videos.list failed: {exc}")

        try:
            caps = yt.captions().list(part="snippet", videoId=vid).execute()
            langs = sorted({c['snippet']['language'] for c in caps.get("items", [])})
            print(f"  Captions ({len(langs)}): {langs}")
        except Exception as exc:
            print(f"  captions.list failed: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["PIH-iTkbk-g", "zzxx1w2LBo4"]))
