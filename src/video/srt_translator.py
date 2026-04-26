"""Translate an SRT caption file to other languages.

Two engines:

* **Claude (preferred)** — preserves proper nouns ($ amounts, "Polymarket",
  team names like "OKC Thunder", trader pseudonyms) and uses the right
  prediction-market vocabulary in each target language. Costs <$0.01 per
  video. Used automatically when ``ANTHROPIC_API_KEY`` is set.
* **Google Translate (fallback)** — free, no key needed, but mangles brand
  names and crypto/Polymarket jargon.

Both produce a byte-for-byte valid SRT (cue indices + timestamps copied
verbatim, only cue text translated) that YouTube accepts via captions.insert.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Iterable, List, Optional

from src.utils.logger import log


_TIMESTAMP_RE = re.compile(r"^\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,.]\d{3}")
_CUE_INDEX_RE = re.compile(r"^\d+$")
# Google Translate supports a 5000-char per-call limit; we batch under that.
_BATCH_CHAR_LIMIT = 4500

_CLAUDE_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

_LANG_NAMES = {
    "en": "English",
    "es": "Spanish",
    "pt": "Portuguese (Brazilian)",
    "hi": "Hindi",
    "id": "Indonesian",
    "tr": "Turkish",
    "fr": "French",
    "de": "German",
    "ja": "Japanese",
    "ko": "Korean",
    "ar": "Arabic",
    "ru": "Russian",
    "vi": "Vietnamese",
    "zh": "Chinese (Simplified)",
}


def translate_srt(
    source_srt: Path,
    target_lang: str,
    source_lang: str = "en",
    output_path: Path | None = None,
) -> Path:
    """Translate an SRT file to ``target_lang``."""
    if output_path is None:
        output_path = source_srt.with_suffix(f".{target_lang}.srt")

    if target_lang == source_lang:
        output_path.write_bytes(source_srt.read_bytes())
        log.info("Captions [{}]: source language, copied verbatim → {}",
                 target_lang, output_path.name)
        return output_path

    raw = source_srt.read_text(encoding="utf-8")
    text_lines, line_kinds = _split_srt(raw)

    payload_indices = [i for i, k in enumerate(line_kinds) if k == "text" and text_lines[i].strip()]
    payloads = [text_lines[i] for i in payload_indices]

    translated = _translate_lines(payloads, source_lang, target_lang)
    if len(translated) != len(payloads):
        log.warning(
            "Translator returned {} lines for {} requested ({}); padding with source.",
            len(translated), len(payloads), target_lang,
        )
        translated = (translated + payloads)[: len(payloads)]

    for idx, new_text in zip(payload_indices, translated):
        text_lines[idx] = new_text

    output_path.write_text("\n".join(text_lines), encoding="utf-8")
    log.info(
        "Captions [{}]: translated {} cues → {}",
        target_lang, len(payload_indices), output_path.name,
    )
    return output_path


def translate_srt_to_many(
    source_srt: Path,
    languages: Iterable[str],
    source_lang: str = "en",
) -> dict[str, Path]:
    """Translate a single SRT into many languages. Returns lang→path."""
    out: dict[str, Path] = {}
    for lang in languages:
        try:
            out[lang] = translate_srt(source_srt, lang, source_lang=source_lang)
        except Exception as exc:  # noqa: BLE001
            log.warning("Skipping language {} — translator failed: {}", lang, exc)
    return out


# ----------------------------------------------------------------------
# Engine dispatch
# ----------------------------------------------------------------------
def _translate_lines(lines: List[str], source_lang: str, target_lang: str) -> List[str]:
    """Translate a list of cue strings; returns same-length output list."""
    if not lines:
        return []

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if api_key:
        try:
            return _translate_with_claude(lines, source_lang, target_lang, api_key)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "Claude translation failed for {}, falling back to Google: {}",
                target_lang, exc,
            )

    return _translate_with_google(lines, source_lang, target_lang)


# ----------------------------------------------------------------------
# Claude engine
# ----------------------------------------------------------------------
_SYSTEM_PROMPT = """You are a professional subtitle translator for short-form
crypto / prediction-market videos.

Rules — apply to every translation:
1. PRESERVE these tokens verbatim, never translate or transliterate them:
   - Brand names: Polymarket, Polygon, Ethereum, Solana, NHL, NBA, MLB, NFL,
     Stanley Cup, Super Bowl, NBA Finals, OKC Thunder, Tampa Bay Lightning,
     etc. — basically any proper noun (team names, league names, market
     platforms, blockchain names).
   - Trader pseudonyms (alphanumeric handles like "kch123", "Theo4",
     "Fredi9999") and wallet addresses (0x...).
   - Currency amounts with $: "$11.8M", "$240K" etc. stay in English form
     for numerals; only the surrounding words get translated.
   - Hashtags (#polymarket, #predictionmarkets) stay verbatim.
2. Translate the surrounding sentence into NATURAL, conversational
   target-language phrasing — not word-for-word. Use the way a sports/
   finance commentator would say it in that language.
3. Keep each line short — these are subtitles, viewers read them in 1–2s.
4. Never add or remove lines; output exactly the same number you receive.
5. Output STRICT JSON only, no prose, no markdown fences."""


def _translate_with_claude(
    lines: List[str], source_lang: str, target_lang: str, api_key: str,
) -> List[str]:
    import anthropic

    target_name = _LANG_NAMES.get(target_lang, target_lang)
    source_name = _LANG_NAMES.get(source_lang, source_lang)

    # Send the cues as an indexed JSON object so Claude can't conflate them.
    indexed = {str(i + 1): line for i, line in enumerate(lines)}
    user_msg = (
        f"Translate these {len(lines)} subtitle cues from {source_name} to "
        f"{target_name}. Return a JSON object with the SAME keys (1, 2, 3, ...) "
        f"and translated string values.\n\n"
        f"INPUT:\n{json.dumps(indexed, ensure_ascii=False, indent=2)}\n\n"
        f"OUTPUT (JSON only, same keys, translated values):"
    )

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=_CLAUDE_MODEL,
        max_tokens=4096,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    text = response.content[0].text.strip()

    # Strip code fences if Claude wrapped it
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE)
    # Snap to the outer {...} block in case prose leaked in.
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group()

    parsed = json.loads(text)
    out: List[str] = []
    for i in range(1, len(lines) + 1):
        v = parsed.get(str(i)) or parsed.get(i)
        if v is None:
            log.warning("Claude omitted cue {} for {}, using source.", i, target_lang)
            out.append(lines[i - 1])
        else:
            out.append(str(v).strip())
    return out


# ----------------------------------------------------------------------
# Google Translate engine (fallback)
# ----------------------------------------------------------------------
def _translate_with_google(
    lines: List[str], source_lang: str, target_lang: str,
) -> List[str]:
    from deep_translator import GoogleTranslator
    translator = GoogleTranslator(source=source_lang, target=target_lang)
    return _google_translate_in_batches(translator, lines)


def _google_translate_in_batches(translator, items: List[str]) -> List[str]:
    """Send strings in <=4.5KB chunks joined with a sentinel."""
    if not items:
        return []
    sentinel = "\n[[CUE_SEP]]\n"
    out: list[str] = []
    buf: list[str] = []
    buf_len = 0

    def flush() -> None:
        if not buf:
            return
        joined = sentinel.join(buf)
        try:
            translated_joined = translator.translate(joined) or ""
        except Exception as exc:  # noqa: BLE001
            log.warning("Google batch failed ({}), per-cue retry.", exc)
            for cue in buf:
                try:
                    out.append(translator.translate(cue) or cue)
                except Exception:
                    out.append(cue)
            buf.clear()
            return
        parts = [p.strip() for p in translated_joined.split(sentinel.strip())]
        if len(parts) != len(buf):
            log.warning(
                "Google sentinel mismatch ({} vs {}), per-cue retry.",
                len(parts), len(buf),
            )
            for cue in buf:
                try:
                    out.append(translator.translate(cue) or cue)
                except Exception:
                    out.append(cue)
        else:
            out.extend(parts)
        buf.clear()

    for item in items:
        addition = len(item) + len(sentinel)
        if buf and buf_len + addition > _BATCH_CHAR_LIMIT:
            flush()
            buf_len = 0
        buf.append(item)
        buf_len += addition
    flush()

    if len(out) < len(items):
        out.extend(items[len(out):])
    return out[: len(items)]


# ----------------------------------------------------------------------
# SRT parsing
# ----------------------------------------------------------------------
def _split_srt(raw: str) -> tuple[list[str], list[str]]:
    """Tag each line as 'index' | 'time' | 'text' | 'blank'."""
    lines = raw.splitlines()
    kinds: list[str] = []
    for line in lines:
        s = line.strip()
        if not s:
            kinds.append("blank")
        elif _CUE_INDEX_RE.match(s):
            kinds.append("index")
        elif _TIMESTAMP_RE.match(s):
            kinds.append("time")
        else:
            kinds.append("text")
    return lines, kinds
