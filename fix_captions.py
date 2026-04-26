"""Regenerate the SRT for an existing run from REAL TTS audio durations,
re-translate it (Claude when key set, Google fallback), then replace the
caption tracks on the YouTube video.

Usage:
    python fix_captions.py output/2026-04-25T14-11-43 MEEaD56SuxA
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from googleapiclient.errors import HttpError

from config.settings import get_settings
from src.utils.logger import log
from src.utils.models import Scene
from src.video.srt_translator import translate_srt_to_many
from src.video.subtitle_generator import SubtitleGenerator
from src.youtube.uploader import (
    API_SERVICE_NAME,
    API_VERSION,
    YouTubeUploader,
)
from googleapiclient.discovery import build


def _measure(path: Path) -> float | None:
    import shutil
    import subprocess
    if not path.exists() or not shutil.which("ffprobe"):
        return None
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, timeout=15,
    )
    if r.returncode != 0:
        return None
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


def main(run_dir_str: str, video_id: str) -> int:
    settings = get_settings()
    run_dir = Path(run_dir_str).resolve()

    scenes_json = run_dir / "scenes.json"
    if not scenes_json.exists():
        log.error("scenes.json missing")
        return 2

    raw = json.loads(scenes_json.read_text(encoding="utf-8"))
    scenes = [
        Scene(
            index=s["index"],
            duration_sec=s["duration_sec"],
            narration=s["narration"],
            on_screen_text=s.get("on_screen_text", ""),
            image_prompt=s.get("image_prompt", ""),
            image_path=Path(s["image_path"]) if s.get("image_path") else None,
        )
        for s in raw
    ]

    # 1) Overwrite each scene's duration with the REAL audio length.
    log.info("Measuring real TTS durations...")
    for i, scene in enumerate(scenes):
        a = run_dir / f"narration_{i:02d}.mp3"
        m = _measure(a)
        if m is not None:
            log.info("  scene {}: predicted={:.2f}s -> actual={:.2f}s",
                     i, scene.duration_sec, m)
            scene.duration_sec = m

    # 2) Rebuild the SRT from the real durations.
    sub_gen = SubtitleGenerator()
    src_srt = run_dir / "subtitles.srt"
    sub_gen.from_scenes(scenes, src_srt)

    # 3) Re-translate (Claude if key set, Google otherwise).
    langs = [c.strip() for c in (settings.caption_languages or "").split(",") if c.strip()]
    log.info("Translating to {}", langs)
    paths = translate_srt_to_many(src_srt, langs, source_lang=langs[0] if langs else "en")

    # 4) Delete existing caption tracks on the video, then re-upload.
    uploader = YouTubeUploader(
        client_secrets_file=Path(settings.youtube_client_secrets_file),
        token_file=Path(settings.youtube_token_file),
    )
    creds = uploader._get_credentials()
    yt = build(API_SERVICE_NAME, API_VERSION, credentials=creds)
    uploader._client = yt

    existing = yt.captions().list(part="id,snippet", videoId=video_id).execute()
    for item in existing.get("items", []):
        cid = item["id"]
        try:
            yt.captions().delete(id=cid).execute()
            log.info("Deleted old caption {} ({})", cid[:20], item["snippet"].get("language"))
        except HttpError as exc:
            log.warning("Could not delete caption {}: {}", cid[:20], exc)

    log.info("Uploading {} fresh caption tracks...", len(paths))
    uploader.upload_captions(
        video_id=video_id,
        srt_paths=paths,
        source_lang=langs[0] if langs else "en",
    )
    log.info("DONE - https://youtu.be/{}", video_id)
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python fix_captions.py <run_dir> <video_id>")
        sys.exit(1)
    sys.exit(main(sys.argv[1], sys.argv[2]))
