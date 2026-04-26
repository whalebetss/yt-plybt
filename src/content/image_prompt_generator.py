"""Enhance image prompts using LLM for better generation results.

The base prompts from SceneBuilder are refined to:
- Add specific visual details
- Ensure consistency with narration
- Optimize for the image generation provider
- Add style keywords for visual appeal
"""
from __future__ import annotations

import anthropic

from src.utils.logger import log


SYSTEM_PROMPT = """You are an expert prompt engineer for AI image generation.

Your task is to enhance image prompts for generating high-quality visuals
for short-form video content. The visuals should be:
- Visually striking and engaging
- Consistent with the narration themes
- Suitable for vertical 9:16 format
- Modern and professional in style

Always add:
- Aspect ratio: 9:16 (vertical)
- High quality style keywords
- Lighting and mood descriptors
- Avoid: text, words, letters in the image"""


USER_PROMPT_TEMPLATE = """Enhance this base prompt for a video scene:

Base prompt: {base_prompt}

Scene narration: {narration}
Scene on-screen text: {on_screen_text}

Provide an enhanced prompt that:
1. Is visually compelling and unique
2. Matches the narrative context
3. Will generate well on OpenAI DALL-E or Stability AI
4. Is optimized for vertical 9:16 format

Respond with ONLY the enhanced prompt, no explanations or markdown."""


class ImagePromptGenerator:
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        self._api_key = api_key
        self._model = model

    def enhance(self, base_prompt: str, narration: str = "",
                on_screen_text: str = "") -> str:
        if not self._api_key:
            return self._add_defaults(base_prompt)

        prompt = USER_PROMPT_TEMPLATE.format(
            base_prompt=base_prompt,
            narration=narration[:200] if narration else "",
            on_screen_text=on_screen_text,
        )

        try:
            client = anthropic.Anthropic(api_key=self._api_key)
            response = client.messages.create(
                model=self._model,
                max_tokens=200,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            enhanced = response.content[0].text.strip()
            if enhanced.startswith("```"):
                enhanced = enhanced[3:]
            if enhanced.endswith("```"):
                enhanced = enhanced[:-3]
            return enhanced.strip()
        except Exception as exc:
            log.warning("Prompt enhancement failed: {}, using base", exc)
            return self._add_defaults(base_prompt)

    def _add_defaults(self, prompt: str) -> str:
        defaults = "9:16 vertical aspect ratio, high quality, trending on artstation, "
        style = "cinematic lighting, dramatic composition, modern digital art"
        return f"{prompt}, {defaults}, {style}"