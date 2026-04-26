"""YouTube metadata (title/description/tags) for the "movers" content type."""
from __future__ import annotations

import json
import re

import anthropic

from src.utils.disclaimers import YOUTUBE_DESCRIPTION_DISCLAIMER
from src.utils.logger import log
from src.utils.models import MoversStory, Script, VideoMetadata


SYSTEM_PROMPT = """You are a YouTube Shorts SEO expert covering crypto +
prediction markets. Generate viral-ready metadata.

Rules:
- Titles under 100 chars, include a number (e.g. "5 markets..."), include
  "Polymarket"
- Descriptions are educational, mention Polymarket explicitly, include the
  educational disclaimer
- Tags: 5-10 relevant keywords (polymarket, prediction markets, polygon, plus
  topical ones drawn from the markets)
- NEVER use "guaranteed" or promise outcomes"""


USER_PROMPT_TEMPLATE = """Generate YouTube Shorts metadata for a video that
narrates these top Polymarket prediction-market movers from the last 24 hours.

SCRIPT HOOK: {hook}
SCRIPT PAYOFF: {payoff}

MARKETS COVERED:
{markets_block}

Generate:
1. title: punchy title under 100 chars, include "Polymarket" + number
2. description: 3-4 short paragraphs. Reference each major market briefly.
   Always include the educational disclaimer.
3. tags: 5-10 relevant tags. Include polymarket, prediction markets, polygon
   plus topical (e.g. "stanley cup", "election", "bitcoin")

Respond as JSON only with fields: title, description, tags"""


class MoversMetadataGenerator:
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        self._api_key = api_key
        self._model = model

    def generate(self, story: MoversStory, script: Script) -> VideoMetadata:
        if not self._api_key:
            return self._fallback(story, script)

        markets_block = _format_block(story)
        try:
            client = anthropic.Anthropic(api_key=self._api_key)
            resp = client.messages.create(
                model=self._model,
                max_tokens=512,
                system=SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": USER_PROMPT_TEMPLATE.format(
                        hook=script.hook,
                        payoff=script.payoff,
                        markets_block=markets_block,
                    ),
                }],
            )
            data = _parse_json(resp.content[0].text.strip())
        except Exception as exc:  # noqa: BLE001
            log.warning("Movers metadata LLM failed: {} — using fallback.", exc)
            return self._fallback(story, script)

        return VideoMetadata(
            title=data.get("title", "")[:100],
            description=data.get("description", ""),
            tags=data.get("tags", []),
            category_id="28",
        )

    # ------------------------------------------------------------------
    def _fallback(self, story: MoversStory, script: Script) -> VideoMetadata:
        biggest = story.movers[0] if story.movers else None
        if biggest:
            sign = "+" if biggest.change_pts > 0 else ""
            title = (
                f"{len(story.movers)} Polymarket markets moved hard in 24h "
                f"({sign}{biggest.change_pts:.0f} pts top swing)"
            )[:100]
        else:
            title = "Today's top Polymarket prediction-market movers"

        bullet_lines = []
        for m in story.movers:
            sign = "+" if m.change_pts > 0 else ""
            bullet_lines.append(
                f"• \"{m.question[:90]}\" — {m.previous_pct}% → {m.implied_pct}% "
                f"({sign}{m.change_pts:.1f} pts)"
            )
        bullets = "\n".join(bullet_lines) or "• (no markets returned)"

        description = f"""🚨 NOT financial advice — educational commentary only.

{script.hook}

Today's biggest 24-hour swings on Polymarket prediction markets (running on Polygon):

{bullets}

Prediction-market shares can lose 100% of their value at resolution. Nothing
in this video is a recommendation to enter, mirror, or fade these positions.

{YOUTUBE_DESCRIPTION_DISCLAIMER}

#polymarket #predictionmarkets #polygon #crypto"""

        tags = [
            "polymarket",
            "prediction markets",
            "polygon",
            "crypto",
            "smart money",
            "betting analytics",
            "market movers",
            "onchain",
            "trading",
        ]

        return VideoMetadata(
            title=title,
            description=description,
            tags=tags,
            category_id="28",
        )


# ---------------------------------------------------------------------------
def _format_block(story: MoversStory) -> str:
    lines = []
    for m in story.movers:
        sign = "+" if m.change_pts > 0 else ""
        lines.append(
            f"- {m.question} | {m.previous_pct}% → {m.implied_pct}% "
            f"({sign}{m.change_pts:.1f} pts) | vol24h ${m.volume_24hr:,.0f}"
        )
    return "\n".join(lines)


def _parse_json(text: str) -> dict:
    text = re.sub(r"[\x00-\x1f]", "", text)
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group())
        raise
