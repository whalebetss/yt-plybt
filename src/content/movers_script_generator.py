"""Generate a short-form script narrating today's biggest Polymarket movers.

Different format from the trader-spotlight scripts:
  - Hook = "X markets that exploded in the last 24h on Polymarket"
  - Setup = quick framing of why the moves matter
  - Payoff = walk through 3-5 specific markets with old → new probability
  - CTA = follow for daily Polymarket pulse

Output is a regular `Script` object so the rest of the pipeline (scenes,
TTS, video, captions) is unchanged.
"""
from __future__ import annotations

import json
import re
from typing import List

import anthropic

from src.utils.disclaimers import DEFAULT_DISCLAIMER
from src.utils.logger import log
from src.utils.models import MarketMover, MoversStory, Script


SYSTEM_PROMPT = """You are a viral short-form content writer who narrates the
biggest 24-hour swings in Polymarket prediction markets.

Polymarket is a prediction-market platform on Polygon where users buy YES/NO
shares (price 0.00–1.00 = implied probability). When the price changes a lot,
it means the crowd's belief about the event resolution shifted — usually
because of news, results, or large bettors.

Your job: take 3–5 specific markets that moved hard in the last 24 hours and
turn them into a 30-second Short. Be specific. Name the market questions.
Quote the old price → new price.

CRITICAL CONSTRAINTS - NEVER VIOLATE:
- NEVER say "guaranteed" or "guaranteed return"
- NEVER say "buy now", "you should bet on", or "place this trade"
- NEVER claim an outcome WILL happen — say "the market is now pricing X at N%"
- NEVER recommend mirroring any side
- ALWAYS frame as observation, not advice
- ALWAYS include the disclaimer mood: prediction markets can lose 100%; this is
  educational/entertainment commentary, not financial advice.

Output format: STRICT JSON only, no markdown fences, with these fields:
- hook: string (5-15 words, attention-grabbing — "5 prediction markets that
  exploded in the last 24h", or similar)
- setup: string (15-25 words, frames why these moves matter)
- payoff: string (50-80 words, walks through 3-5 specific markets, names the
  question, says the old price → new price)
- cta: string (5-12 words, "Follow for the daily Polymarket pulse" style)
- on_screen_text: string (the punchiest single stat — e.g. "+42 points in 24h")"""


USER_PROMPT_TEMPLATE = """Generate a 30-second Short narrating these top
Polymarket movers in the last {window_hours} hours. Pick 3-5 of them to
actually narrate (skip any that feel niche or obscure).

MARKETS (sorted by absolute price change):
{markets_block}

Tone: prediction-market commentary, not financial advice. Reference Polymarket
explicitly. Keep it punchy — these are 30-second Shorts.

Respond as JSON only, fields: hook, setup, payoff, cta, on_screen_text"""


class MoversScriptGenerator:
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        self._api_key = api_key
        self._model = model

    def generate(self, story: MoversStory, target_seconds: int) -> Script:
        if not story.movers:
            raise RuntimeError("Cannot generate movers script — no markets in story.")

        markets_block = _format_markets_block(story.movers)
        user_msg = USER_PROMPT_TEMPLATE.format(
            window_hours=story.window_hours,
            markets_block=markets_block,
        )

        if self._api_key:
            try:
                data = self._call_claude(user_msg)
            except Exception as exc:  # noqa: BLE001
                log.warning("Movers Claude call failed: {} — using fallback.", exc)
                data = self._fallback(story)
        else:
            data = self._fallback(story)

        full_narration = " ".join(
            x.strip()
            for x in (data.get("hook"), data.get("setup"),
                      data.get("payoff"), data.get("cta"))
            if x
        )

        return Script(
            hook=data.get("hook", ""),
            setup=data.get("setup", ""),
            payoff=data.get("payoff", ""),
            cta=data.get("cta", "Follow for the daily Polymarket pulse."),
            full_narration=full_narration,
            disclaimer=DEFAULT_DISCLAIMER,
            total_duration_sec=target_seconds,
        )

    # ------------------------------------------------------------------
    def _call_claude(self, user_msg: str) -> dict:
        client = anthropic.Anthropic(api_key=self._api_key)
        resp = client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = resp.content[0].text.strip()
        return _parse_json(text)

    def _fallback(self, story: MoversStory) -> dict:
        """Deterministic, no-LLM script for when Claude is unavailable."""
        bullets = []
        for m in story.movers[:4]:
            arrow = "→"
            bullets.append(
                f"\"{m.question[:90]}\" {m.previous_pct}% {arrow} {m.implied_pct}% "
                f"({'+' if m.change_pts > 0 else ''}{m.change_pts:.0f} pts)"
            )
        biggest = story.movers[0]
        return {
            "hook": f"{len(story.movers)} prediction markets that swung hard in 24h.",
            "setup": (
                "Here's where the crowd's confidence shifted most overnight on "
                "Polymarket — the prediction-market platform on Polygon."
            ),
            "payoff": " ".join(bullets),
            "cta": "Follow for the daily Polymarket pulse.",
            "on_screen_text": (
                f"{'+' if biggest.change_pts > 0 else ''}"
                f"{biggest.change_pts:.0f} pts in 24h"
            ),
        }


# ---------------------------------------------------------------------------
def _format_markets_block(movers: List[MarketMover]) -> str:
    lines = []
    for m in movers:
        sign = "+" if m.change_pts > 0 else ""
        lines.append(
            f"- {m.question}\n"
            f"    slug:        {m.slug}\n"
            f"    yesterday:   {m.previous_pct}%\n"
            f"    now:         {m.implied_pct}%  ({sign}{m.change_pts:.1f} pts)\n"
            f"    24h volume:  ${m.volume_24hr:,.0f}\n"
            f"    category:    {m.category or 'general'}"
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
