"""Re-render video.mp4 from a run_dir's assets WITHOUT burning subtitles.

Reads scenes.json + narration.mp3 (already concatenated) and runs the
FFmpegAssembler with subtitle_path=None. The .srt is left untouched on
disk because we still want to upload it as a YouTube caption track.

Usage:
    python rerender.py output/2026-04-25T00-28-44
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from src.utils.logger import log
from src.utils.models import Scene
from src.video.ffmpeg_assembler import FFmpegAssembler


def main(run_dir_str: str) -> int:
    run_dir = Path(run_dir_str).resolve()
    scenes_json = run_dir / "scenes.json"
    narration_path = run_dir / "narration.mp3"

    if not scenes_json.exists():
        log.error("scenes.json missing in {}", run_dir)
        return 2
    if not narration_path.exists():
        log.error("narration.mp3 missing in {}", run_dir)
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
    log.info("Re-rendering {} scenes (NO subtitle burn-in)...", len(scenes))

    output_path = run_dir / "video.mp4"
    # Back up the old burn-in version just in case.
    if output_path.exists():
        backup = output_path.with_suffix(".burned.mp4.bak")
        output_path.replace(backup)
        log.info("Old (burned) video backed up to {}", backup.name)

    assembler = FFmpegAssembler()
    assembler.assemble(
        scenes,
        output_path,
        narration_path=narration_path,
        subtitle_path=None,  # <-- the whole point
        vertical=True,
    )
    log.info("Clean video re-rendered: {} ({} bytes)",
             output_path, output_path.stat().st_size)
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python rerender.py <run_dir>")
        sys.exit(1)
    sys.exit(main(sys.argv[1]))
