"""Assemble a vertical 9:16 short from per-scene images, narration, and subs.

The previous version only used the first scene image. This rewrite:

1. Builds a `concat` demuxer file listing every scene image with its duration.
2. Scales + pads each frame to exactly 1080x1920 (no distortion, black bars).
3. Mixes the narration audio in.
4. Burns the SRT subtitles directly into the video so YouTube Shorts always
   shows them (uploaded captions don't render on Shorts thumbnails).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from src.utils.logger import log
from src.utils.models import Scene


WIDTH = 1080
HEIGHT = 1920
FPS = 30


class FFmpegAssembler:
    def assemble(
        self,
        scenes: List[Scene],
        output_path: Path,
        narration_path: Optional[Path] = None,
        subtitle_path: Optional[Path] = None,
        vertical: bool = True,  # kept for API compatibility; always 9:16
    ) -> Path:
        if not scenes:
            raise RuntimeError("No scenes supplied to FFmpegAssembler.")
        if not shutil.which("ffmpeg"):
            raise RuntimeError("ffmpeg is not on PATH.")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        usable = [s for s in scenes if s.image_path and s.image_path.exists()]
        if not usable:
            raise RuntimeError("No scene images exist on disk.")

        # 1) Build a silent slideshow video from the scene images.
        slideshow = output_path.with_suffix(".slideshow.mp4")
        self._render_slideshow(usable, slideshow)

        # 2) Mux narration + burn subtitles in a single second pass.
        self._finalize(slideshow, output_path, narration_path, subtitle_path)

        # Clean up the intermediate file.
        try:
            slideshow.unlink(missing_ok=True)
        except OSError:
            pass

        log.info("Video done: {}", output_path)
        return output_path

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------
    def _render_slideshow(self, scenes: List[Scene], slideshow: Path) -> None:
        """Concat-demuxer based slideshow: each image gets its own duration."""
        run_dir = slideshow.parent
        concat_file = run_dir / "concat_images.txt"
        with concat_file.open("w", encoding="utf-8") as f:
            for s in scenes:
                # ffmpeg concat demuxer: ANSI single quotes, escape backslashes.
                path = str(s.image_path.absolute()).replace("\\", "/")
                f.write(f"file '{path}'\n")
                f.write(f"duration {max(s.duration_sec, 0.5):.3f}\n")
            # The concat demuxer ignores the last `duration`, so we repeat
            # the final image without one to flush the timeline.
            last = str(scenes[-1].image_path.absolute()).replace("\\", "/")
            f.write(f"file '{last}'\n")

        vf = (
            f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
            f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black,"
            "setsar=1,format=yuv420p"
        )

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_file),
            "-vf", vf,
            "-r", str(FPS),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(slideshow),
        ]
        log.debug("FFmpeg slideshow cmd: {}", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            log.error("Slideshow render failed:\n{}", result.stderr[-1000:])
            raise RuntimeError("FFmpeg slideshow step failed.")

    def _finalize(
        self,
        slideshow: Path,
        output_path: Path,
        narration_path: Optional[Path],
        subtitle_path: Optional[Path],
    ) -> None:
        cmd: list[str] = ["ffmpeg", "-y", "-i", str(slideshow)]

        has_audio = narration_path is not None and narration_path.exists()
        if has_audio:
            cmd += ["-i", str(narration_path)]

        if subtitle_path is not None and subtitle_path.exists():
            cmd += ["-vf", _subtitle_filter(subtitle_path)]
        else:
            cmd += ["-c:v", "copy"]

        if has_audio:
            cmd += [
                "-c:a", "aac", "-b:a", "192k",
                "-map", "0:v:0", "-map", "1:a:0",
                "-shortest",
            ]
        else:
            cmd += ["-an"]

        cmd += ["-movflags", "+faststart", str(output_path)]

        log.debug("FFmpeg finalize cmd: {}", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            log.error("Finalize step failed:\n{}", result.stderr[-1000:])
            raise RuntimeError("FFmpeg finalize step failed.")


def _subtitle_filter(srt: Path) -> str:
    """Build a `subtitles=` filter string with the path properly escaped.

    On Windows ffmpeg's libass needs the drive colon escaped, like:
        subtitles=C\\:/Users/.../subs.srt
    Otherwise libass thinks `C:` is a filter option separator.
    """
    p = str(srt.absolute()).replace("\\", "/")
    # Escape the colon that follows the drive letter.
    if len(p) > 1 and p[1] == ":":
        p = p[0] + r"\:" + p[2:]
    style = (
        "FontName=Arial,FontSize=18,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=3,Outline=2,Shadow=0,"
        "Alignment=2,MarginV=120"
    )
    return f"subtitles='{p}':force_style='{style}'"
