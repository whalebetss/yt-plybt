"""Break down script into scenes with timing and prompts.

Scene structure for 55-second video (optimized for retention):
- Scene 0 (0-3s): HOOK - Visual hook / question
- Scene 1 (3-10s): SETUP - Wallet intro with stats overlay
- Scene 2 (10-25s): PAYOFF Part 1 - Main pattern/insight
- Scene 3 (25-40s): PAYOFF Part 2 - Deep dive
- Scene 4 (40-50s): PAYOFF Part 3 - Key takeaway
- Scene 5 (50-55s): CTA - Subscribe prompt

Each scene has:
- duration_sec: how long it displays
- narration: what voice reads
- on_screen_text: key text overlay
- image_prompt: description for image gen
"""
from __future__ import annotations

from pathlib import Path
from typing import List

from src.utils.logger import log
from src.utils.models import Scene, Script


class SceneBuilder:
    SCENE_TIMINGS = [
        (0, 3, "hook"),
        (3, 7, "setup"),
        (7, 20, "payoff_1"),
        (20, 35, "payoff_2"),
        (35, 48, "payoff_3"),
        (48, 55, "cta"),
    ]

    def build(self, script: Script, target_duration: int = 55, run_dir: Path = None) -> List[Scene]:
        scenes = []
        if run_dir is None:
            run_dir = Path(".")

        # Split the payoff into ~3 chunks of words for the three payoff scenes,
        # so we don't accidentally re-narrate the hook/cta during the body.
        payoff_chunks = self._split_payoff(script.payoff or script.full_narration, 3)

        for i, (start, end, phase) in enumerate(self.SCENE_TIMINGS):
            if start >= target_duration:
                break
            duration = min(end - start, target_duration - start)

            phase_text = self._extract_phase_text(phase, script, payoff_chunks)
            on_screen = self._extract_on_screen(phase, script)
            image_prompt = self._generate_image_prompt(phase, script)

            scenes.append(
                Scene(
                    index=i,
                    duration_sec=duration,
                    narration=phase_text,
                    on_screen_text=on_screen,
                    image_prompt=image_prompt,
                    image_path=None,
                )
            )

        for i, scene in enumerate(scenes):
            scene.image_path = run_dir / "images" / f"scene_{i:02d}.png"
            scene.image_path.parent.mkdir(parents=True, exist_ok=True)

        log.info("Built {} scenes", len(scenes))
        return scenes

    @staticmethod
    def _split_payoff(payoff: str, parts: int) -> list[str]:
        words = (payoff or "").split()
        if not words:
            return [""] * parts
        per_chunk = max(1, len(words) // parts)
        chunks = [
            " ".join(words[i * per_chunk : (i + 1) * per_chunk]) for i in range(parts)
        ]
        # Append leftover words to the last chunk so we don't drop content.
        leftover = words[parts * per_chunk :]
        if leftover:
            chunks[-1] = (chunks[-1] + " " + " ".join(leftover)).strip()
        return chunks

    def _extract_phase_text(
        self,
        phase: str,
        script: Script,
        payoff_chunks: list[str],
    ) -> str:
        if phase == "hook":
            return script.hook
        if phase == "setup":
            return script.setup
        if phase == "cta":
            return script.cta
        idx = {"payoff_1": 0, "payoff_2": 1, "payoff_3": 2}.get(phase, 0)
        return payoff_chunks[idx] if idx < len(payoff_chunks) else ""

    def _extract_on_screen(self, phase: str, script: Script) -> str:
        if phase == "hook":
            return script.hook[:50]
        elif phase == "setup":
            return "Wallet Activity"
        elif phase == "payoff_1":
            return "The Strategy"
        elif phase == "payoff_2":
            return "Key Pattern"
        elif phase == "payoff_3":
            return "What We Learned"
        elif phase == "cta":
            return "Follow for more!"
        return ""

    def _generate_image_prompt(self, phase: str, script: Script) -> str:
        prompts = {
            "hook": "Abstract question mark with crypto symbols, dark background, neon accents, trending on social media",
            "setup": "Blockchain network visualization, connected nodes, data streams, professional crypto aesthetic",
            "payoff_1": "Trading chart analysis, pattern recognition, geometric shapes overlay, modern fintech visualization",
            "payoff_2": "Abstract financial data stream, connections between wallets, network graph, sleek dark theme",
            "payoff_3": "Key insight visualization, lightbulb with blockchain, clean modern design, minimal",
            "cta": "Subscribe button aesthetic, follow icon, vibrant gradient background, engaging",
        }
        return prompts.get(phase, "Abstract crypto visualization, modern dark theme")