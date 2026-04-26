"""SRT subtitle generator.

A scene's narration can be a long sentence; if we render it as a single SRT
cue, libass wraps it across the full frame and unreadable walls of text are
the result on Shorts. We split each scene into multiple short cues
(~6 words each) and distribute the scene's total duration across them.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

from src.utils.logger import log
from src.utils.models import Scene


WORDS_PER_CUE = 6        # max ~2 lines on a 9:16 frame at FontSize=18
MIN_CUE_SECONDS = 0.6    # don't blink past readability
MAX_CUE_SECONDS = 4.0    # never freeze on a single cue too long


class SubtitleGenerator:
    def from_scenes(self, scenes: List[Scene], output_path: Path) -> bool:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        srt = self._build_srt(scenes)
        output_path.write_text(srt, encoding="utf-8")
        log.info("Subtitles saved to {}", output_path)
        return True

    def _build_srt(self, scenes: List[Scene]) -> str:
        lines: list[str] = []
        cue_idx = 1
        cursor = 0.0

        for scene in scenes:
            scene_start = cursor
            scene_end = scene_start + scene.duration_sec

            chunks = self._chunk_words(scene.narration, WORDS_PER_CUE)
            if not chunks:
                cursor = scene_end
                continue

            # Distribute the scene's actual duration across its chunks
            # PROPORTIONALLY (by word count) so longer cues get more
            # screen-time. Then clamp each cue to readability bounds and
            # never let it overshoot scene_end — overshooting is what made
            # subsequent scenes drift behind their narration.
            word_counts = [max(1, len(c.split())) for c in chunks]
            total_words = sum(word_counts)

            for chunk, words in zip(chunks, word_counts):
                share = scene.duration_sec * (words / total_words)
                share = max(MIN_CUE_SECONDS, min(MAX_CUE_SECONDS, share))
                start = cursor
                end = min(start + share, scene_end)
                if end <= start:
                    end = min(start + MIN_CUE_SECONDS, scene_end)

                lines.append(str(cue_idx))
                lines.append(f"{_ts(start)} --> {_ts(end)}")
                lines.append(chunk)
                lines.append("")
                cue_idx += 1
                cursor = end

            # Anchor the cursor at the true scene boundary so the next
            # scene's first cue lines up with the next narration clip.
            cursor = scene_end

        return "\n".join(lines)

    @staticmethod
    def _chunk_words(text: str, n: int) -> list[str]:
        words = (text or "").split()
        return [" ".join(words[i : i + n]) for i in range(0, len(words), n)]


def _ts(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms == 1000:
        ms = 0
        s += 1
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
