"""Generate images from text prompts.

Providers: OpenAI DALL-E, Stability AI, Leonardo AI, Hugging Face, or placeholder.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import requests

from src.utils.logger import log


class ImageGenerator:
    PROVIDERS = Literal["openai", "stability", "leonardo", "huggingface", "none"]

    def __init__(
        self,
        provider: PROVIDERS = "none",
        stability_api_key: str = "",
        leonardo_api_key: str = "",
        huggingface_api_key: str = "",
        openai_api_key: str = "",
    ):
        self._provider = provider
        self._stability_key = stability_api_key
        self._leonardo_key = leonardo_api_key
        self._hf_key = huggingface_api_key
        self._openai_key = openai_api_key

    def generate(self, prompt: str, output_path: Path, scene_index: int = 0, wallet_data: dict = None) -> bool:
        if self._provider == "none":
            log.info("Image generation disabled - creating placeholder")
            return self._create_placeholder(output_path, scene_index, wallet_data)

        if self._provider == "openai":
            return self._generate_openai(prompt, output_path)
        elif self._provider == "stability":
            return self._generate_stability(prompt, output_path)
        elif self._provider == "leonardo":
            return self._generate_leonardo(prompt, output_path)
        elif self._provider == "huggingface":
            return self._generate_huggingface(prompt, output_path)
        else:
            log.error("Unknown provider: {}", self._provider)
            return False

    def _generate_openai(self, prompt: str, output_path: Path) -> bool:
        from openai import OpenAI

        if not self._openai_key:
            log.warning("OpenAI key missing - using placeholder")
            return self._create_placeholder(output_path)

        client = OpenAI(api_key=self._openai_key)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            response = client.images.generate(
                model="dall-e-3",
                prompt=prompt,
                size="1024x1792",
                quality="standard",
                n=1,
            )
            image_url = response.data[0].url
            if not image_url:
                log.error("No image URL returned")
                return self._create_placeholder(output_path)

            img_data = self._download_image(image_url)
            if not img_data:
                return self._create_placeholder(output_path)

            output_path.write_bytes(img_data)
            log.info("OpenAI image saved to {}", output_path)
            return True
        except Exception as exc:
            log.error("OpenAI image failed: {}", exc)
            return self._create_placeholder(output_path)

    def _generate_stability(self, prompt: str, output_path: Path) -> bool:
        if not self._stability_key:
            log.warning("Stability key missing - using placeholder")
            return self._create_placeholder(output_path)

        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            response = requests.post(
                "https://api.stability.ai/v2beta/stable-image/generate/core",
                headers={
                    "Authorization": f"Bearer {self._stability_key}",
                    "Accept": "image/*",
                },
                files={"none": ""},  # required by the multipart endpoint
                data={
                    "prompt": prompt,
                    "aspect_ratio": "9:16",
                    "output_format": "png",
                },
                timeout=60,
            )
        except requests.RequestException as exc:
            log.error("Stability request failed: {}", exc)
            return self._create_placeholder(output_path)

        if response.status_code != 200:
            log.error(
                "Stability error: {} - {}",
                response.status_code,
                response.text[:200],
            )
            return self._create_placeholder(output_path)

        output_path.write_bytes(response.content)
        log.info("Stability image saved to {}", output_path)
        return True

    def _generate_leonardo(self, prompt: str, output_path: Path) -> bool:
        if not self._leonardo_key:
            log.warning("Leonardo key missing - using placeholder")
            return self._create_placeholder(output_path)

        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            response = requests.post(
                "https://cloud.leonardo.ai/api/v1/generations",
                headers={
                    "Authorization": f"Bearer {self._leonardo_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "prompt": prompt,
                    "width": 1024,
                    "height": 1792,
                    "model_uuid": "ac5b0496-6c4b-4d69-8c51-a67d564e93cb",
                },
                timeout=60,
            )

            log.info("Leonardo response: {} - {}", response.status_code, response.text[:200])

            if response.status_code != 200:
                log.error("Leonardo error: {} - {}", response.status_code, response.text)
                return self._create_placeholder(output_path)

            gen_id = response.json()["generation"]["id"]

            for _ in range(30):
                result = requests.get(
                    f"https://cloud.leonardo.ai/api/v1/generations/{gen_id}",
                    headers={"Authorization": f"Bearer {self._leonardo_key}"},
                )
                if result.status_code != 200:
                    break
                status = result.json()["generation"]["status"]
                if status == "COMPLETE":
                    img_url = result.json()["generation"]["generated_images"][0]["url"]
                    img_data = self._download_image(img_url)
                    if img_data:
                        output_path.write_bytes(img_data)
                        log.info("Leonardo image saved to {}", output_path)
                        return True
                    break
                elif status == "FAILED":
                    break
                import time
                time.sleep(2)

            return self._create_placeholder(output_path)
        except Exception as exc:
            log.error("Leonardo failed: {}", exc)
            return self._create_placeholder(output_path)

    def _download_image(self, url: str) -> bytes:
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            return resp.content
        except Exception as exc:
            log.error("Download failed: {}", exc)
            return b""

    def _generate_huggingface(self, prompt: str, output_path: Path) -> bool:
        if not self._hf_key:
            log.warning("HuggingFace key missing - using placeholder")
            return self._create_placeholder(output_path)

        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            API_URL = "https://api-inference.huggingface.co/models/stabilityai/stable-diffusion-xl-base-1.0"
            
            response = requests.post(
                API_URL,
                headers={"Authorization": f"Bearer {self._hf_key}"},
                json={"inputs": prompt},
                timeout=120,
            )

            if response.status_code != 200:
                log.error("HuggingFace error: {} - {}", response.status_code, response.text[:200])
                return self._create_placeholder(output_path)

            img_data = response.content
            if not img_data:
                return self._create_placeholder(output_path)

            output_path.write_bytes(img_data)
            log.info("HuggingFace image saved to {}", output_path)
            return True
        except Exception as exc:
            log.error("HuggingFace failed: {}", exc)
            return self._create_placeholder(output_path)

    def _create_placeholder(self, output_path: Path, scene_index: int = 0, wallet_data: dict = None) -> bool:
        output_path.parent.mkdir(parents=True, exist_ok=True)

        wd = wallet_data or {}
        is_polymarket = "polymarket" in (wd.get("sources") or [])
        try:
            if is_polymarket:
                return _render_polymarket_placeholder(output_path, scene_index, wd)
            return _render_dex_placeholder(output_path, scene_index, wd)
        except Exception as exc:
            log.warning("Could not create placeholder: {}", exc)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"placeholder")
            return True


# ---------------------------------------------------------------------------
# PIL font helper: tries a handful of common system fonts before falling back
# to PIL's built-in bitmap (which is 10px and unreadable at 1080x1920).
# ---------------------------------------------------------------------------
_FONT_CANDIDATES = (
    "arialbd.ttf", "arial.ttf",          # Windows
    "DejaVuSans-Bold.ttf", "DejaVuSans.ttf",  # Linux
    "/System/Library/Fonts/Helvetica.ttc",    # macOS
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
)


def _load_font(size: int):
    from PIL import ImageFont
    for name in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, size)
        except (OSError, IOError):
            continue
    # Last-resort fallback: PIL's default bitmap font. Ugly but safe.
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Placeholder renderers (one per data source flavor).
# ---------------------------------------------------------------------------
_PHASE_LABELS = {0: "HOOK", 1: "SETUP", 2: "ANALYSIS", 3: "DEEP DIVE", 4: "KEY TAKEAWAY", 5: "CTA"}
_PHASE_COLORS = {
    0: (0, 200, 255),
    1: (150, 100, 255),
    2: (255, 200, 0),
    3: (0, 255, 150),
    4: (255, 100, 150),
    5: (100, 200, 255),
}


def _draw_grid(draw, W: int, H: int) -> None:
    for y in range(0, H, 90):
        draw.line([(0, y), (W, y)], fill=(40, 40, 70), width=1)
    for x in range(0, W, 90):
        draw.line([(x, 0), (x, H)], fill=(40, 40, 70), width=1)


def _wrap_lines(text: str, max_chars: int) -> list[str]:
    words = (text or "").split()
    lines: list[str] = []
    current = ""
    for w in words:
        candidate = (current + " " + w).strip()
        if len(candidate) > max_chars and current:
            lines.append(current)
            current = w
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _render_dex_placeholder(output_path: Path, scene_index: int, wd: dict) -> bool:
    from PIL import Image, ImageDraw

    raw_addr = wd.get("address", "")
    addr = (raw_addr[:6] + "…" + raw_addr[-4:]) if raw_addr else "0x0000…0000"
    chain = (wd.get("chain") or "ethereum").upper()
    roi = wd.get("roi_percent", 0)
    wr = wd.get("winrate", 0) * 100 if wd.get("winrate", 0) <= 1 else wd.get("winrate", 0)
    trades = wd.get("trades", 0)
    pnl = wd.get("pnl_usd", 0)
    label = wd.get("label") or "Anonymous Wallet"

    W, H = 1080, 1920
    img = Image.new("RGB", (W, H), color=(10, 10, 25))
    draw = ImageDraw.Draw(img)
    _draw_grid(draw, W, H)

    font_xl = _load_font(140)
    font_lg = _load_font(96)
    font_md = _load_font(64)
    font_sm = _load_font(44)
    font_xs = _load_font(36)

    phase = _PHASE_LABELS.get(scene_index, "SCENE")
    phase_color = _PHASE_COLORS.get(scene_index, (150, 150, 200))

    draw.text((W // 2, 130), phase, fill=phase_color, font=font_md, anchor="mm")
    draw.text((W // 2, 260), label, fill=(220, 220, 255), font=font_lg, anchor="mm")
    draw.text((W // 2, 360), addr, fill=(180, 180, 220), font=font_sm, anchor="mm")
    draw.text((W // 2, 430), chain, fill=(150, 150, 200), font=font_xs, anchor="mm")

    draw.text((W // 2, 720), f"${pnl:,.0f}", fill=(0, 255, 150), font=font_xl, anchor="mm")
    draw.text((W // 2, 830), "Total PnL", fill=(150, 150, 200), font=font_sm, anchor="mm")

    draw.text((W // 4, 1080), f"{trades:,}", fill=(255, 200, 0), font=font_lg, anchor="mm")
    draw.text((W // 4, 1170), "Trades", fill=(150, 150, 200), font=font_sm, anchor="mm")

    draw.text((3 * W // 4, 1080), f"{wr:.0f}%", fill=(255, 120, 120), font=font_lg, anchor="mm")
    draw.text((3 * W // 4, 1170), "Win Rate", fill=(150, 150, 200), font=font_sm, anchor="mm")

    draw.text((W // 2, 1360), f"{roi:.0f}% ROI", fill=(0, 200, 255), font=font_lg, anchor="mm")

    draw.text((W // 2, 1780), "Not Financial Advice", fill=(255, 120, 120), font=font_sm, anchor="mm")
    draw.text((W // 2, 1840), "Educational Content Only", fill=(160, 120, 120), font=font_xs, anchor="mm")

    img.save(output_path)
    log.info("Created wallet info placeholder: {}", output_path)
    return True


def _render_polymarket_placeholder(output_path: Path, scene_index: int, wd: dict) -> bool:
    """Render a Polymarket-flavored card.

    HOOK / SETUP / CTA scenes show the trader headline (PnL + winrate).
    The middle "payoff" scenes (index 2-4) each highlight one open position
    so the viewer sees the actual market the bot is narrating about.
    """
    from PIL import Image, ImageDraw

    label = wd.get("label") or "Anonymous Polymarket Trader"
    pnl = wd.get("pnl_usd", 0)
    wr = wd.get("winrate", 0) * 100 if wd.get("winrate", 0) <= 1 else wd.get("winrate", 0)
    trades = wd.get("trades", 0)
    positions = wd.get("positions") or []

    W, H = 1080, 1920
    img = Image.new("RGB", (W, H), color=(8, 8, 30))
    draw = ImageDraw.Draw(img)
    _draw_grid(draw, W, H)

    font_xl = _load_font(140)
    font_lg = _load_font(88)
    font_md = _load_font(60)
    font_sm = _load_font(44)
    font_xs = _load_font(36)
    font_xxs = _load_font(28)

    phase = _PHASE_LABELS.get(scene_index, "SCENE")
    phase_color = _PHASE_COLORS.get(scene_index, (150, 150, 200))

    # Top band: phase + trader name + Polymarket source badge.
    draw.text((W // 2, 130), phase, fill=phase_color, font=font_md, anchor="mm")
    draw.text((W // 2, 250), label, fill=(220, 220, 255), font=font_lg, anchor="mm")
    draw.text((W // 2, 340), "POLYMARKET · POLYGON", fill=(150, 150, 200), font=font_xs, anchor="mm")

    # Pick which open position to feature (if any). Indexes 2/3/4 highlight
    # the first three positions; everything else falls back to the headline.
    feature_position = None
    if scene_index in (2, 3, 4) and positions:
        idx = scene_index - 2
        if idx < len(positions):
            feature_position = positions[idx]

    if feature_position:
        market = str(feature_position.get("market_question") or "Open market")
        outcome = str(feature_position.get("outcome") or "?").upper()
        cur_price = float(feature_position.get("current_price") or 0.0)
        avg_price = float(feature_position.get("avg_entry_price") or 0.0)
        shares = float(feature_position.get("shares") or 0.0)
        pos_pnl = float(feature_position.get("pnl_usd") or 0.0)
        implied = int(round(cur_price * 100))
        exposure = shares * (cur_price or avg_price)

        # Wrap the market question across up to 4 lines so it stays inside the frame.
        wrapped = _wrap_lines(market, max_chars=22)[:4]
        y = 540
        for line in wrapped:
            draw.text((W // 2, y), line, fill=(255, 255, 255), font=font_md, anchor="mm")
            y += 80

        # Side badge (YES/NO).
        side_color = (0, 220, 140) if outcome.startswith("Y") else (255, 110, 110)
        draw.text((W // 2, y + 40), outcome, fill=side_color, font=font_xl, anchor="mm")
        draw.text((W // 2, y + 170), "Position side", fill=(150, 150, 200), font=font_sm, anchor="mm")

        # Implied probability + exposure stats.
        draw.text((W // 4, 1380), f"{implied}%", fill=(0, 200, 255), font=font_lg, anchor="mm")
        draw.text((W // 4, 1470), "Implied", fill=(150, 150, 200), font=font_sm, anchor="mm")

        draw.text((3 * W // 4, 1380), f"${exposure:,.0f}", fill=(255, 200, 0), font=font_lg, anchor="mm")
        draw.text((3 * W // 4, 1470), "Exposure", fill=(150, 150, 200), font=font_sm, anchor="mm")

        pnl_color = (0, 220, 140) if pos_pnl >= 0 else (255, 110, 110)
        draw.text((W // 2, 1620), f"PnL ${pos_pnl:,.0f}", fill=pnl_color, font=font_md, anchor="mm")
    else:
        # Headline scene: total realized PnL + winrate over recent trades.
        pnl_color = (0, 255, 150) if pnl >= 0 else (255, 110, 110)
        draw.text((W // 2, 720), f"${pnl:,.0f}", fill=pnl_color, font=font_xl, anchor="mm")
        draw.text((W // 2, 830), "Realized PnL", fill=(150, 150, 200), font=font_sm, anchor="mm")

        draw.text((W // 4, 1080), f"{wr:.0f}%", fill=(255, 200, 0), font=font_lg, anchor="mm")
        draw.text((W // 4, 1170), "Winrate", fill=(150, 150, 200), font=font_sm, anchor="mm")

        draw.text((3 * W // 4, 1080), f"{trades:,}", fill=(0, 200, 255), font=font_lg, anchor="mm")
        draw.text((3 * W // 4, 1170), "Trades", fill=(150, 150, 200), font=font_sm, anchor="mm")

        # Tease the markets we'll cover later in the video.
        draw.text((W // 2, 1340), f"{len(positions)} open markets", fill=(220, 220, 255), font=font_md, anchor="mm")
        teaser = positions[:2]
        y = 1430
        for pos in teaser:
            q = str(pos.get("market_question") or "")
            for line in _wrap_lines(q, max_chars=32)[:2]:
                draw.text((W // 2, y), line, fill=(180, 180, 220), font=font_xxs, anchor="mm")
                y += 38
            y += 14

    draw.text((W // 2, 1780), "Not Financial Advice", fill=(255, 120, 120), font=font_sm, anchor="mm")
    draw.text((W // 2, 1840), "Prediction-market commentary, educational only", fill=(160, 120, 120), font=font_xs, anchor="mm")

    img.save(output_path)
    log.info("Created Polymarket placeholder: {}", output_path)
    return True