"""Main pipeline orchestrator.

Flow:
    1. Fetch wallets from Dune (and optionally Arkham)
    2. Filter by ROI/winrate/consistency
    3. Validate data cross-reference
    4. Generate script (structured storytelling)
    5. Create scene breakdown
    6. Generate image prompts -> images
    7. Generate TTS narration + subtitles
    8. Assemble video with FFmpeg
    9. Generate metadata
    10. Upload to YouTube (unless dry_run)
"""
from __future__ import annotations

import shutil
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.utils.logger import log
from src.utils.models import (
    PipelineResult,
    Script,
    VideoMetadata,
    WalletProfile,
    dump_json,
)
from config.settings import Settings


def run_pipeline(settings: Settings) -> Optional[PipelineResult]:
    """Run the full pipeline. Returns None if no qualifying wallets."""
    _check_ffmpeg(settings)

    run_id = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    run_dir = settings.output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    log.info("Run directory: {}", run_dir)

    wallets = _fetch_wallets(settings)
    if not wallets:
        log.warning("No wallets fetched - aborting pipeline.")
        return None

    dump_json(wallets, run_dir / "wallets.json")

    # Dedup against previous runs: drop wallets/position-sets we already
    # featured recently. The store is shared across all daily cron firings.
    history = _open_history(settings)
    fresh = history.filter_unseen(wallets)
    log.info(
        "Dedup: {}/{} wallets remain after history filter "
        "(wallet_lookback={}d, position_lookback={}d)",
        len(fresh), len(wallets),
        settings.wallet_lookback_days, settings.position_lookback_days,
    )
    if not fresh:
        log.warning(
            "All %d candidate wallets were already featured recently — "
            "consider widening the leaderboard scan or shortening lookback.",
            len(wallets),
        )
        return None

    wallet = _select_wallet(fresh, settings)
    if not wallet:
        log.warning("No qualifying wallet after filtering.")
        return None

    log.info("Selected wallet: {} (ROI: {:.1f}%, Winrate: {:.0f}%)",
             wallet.display_name, wallet.roi_percent, wallet.winrate * 100)

    script = _generate_script(wallet, settings, run_dir)
    dump_json(asdict(script), run_dir / "script.json")

    scenes = _build_scenes(script, settings, run_dir)
    dump_json([asdict(s) for s in scenes], run_dir / "scenes.json")

    _generate_images(scenes, settings, wallet, run_dir)
    _generate_narration(scenes, settings, run_dir)

    video_path = _assemble_video(scenes, settings, run_dir)

    metadata = _generate_metadata(wallet, script, settings)
    dump_json(asdict(metadata), run_dir / "metadata.json")

    # Translate the SRT into the configured caption languages. We do this
    # *before* the upload so a translation glitch never blocks the video.
    caption_paths = _translate_captions(run_dir, settings)

    youtube_video_id = None
    if not settings.dry_run:
        youtube_video_id = _upload(
            video_path, metadata, settings, run_dir,
            caption_paths=caption_paths,
        )
    else:
        log.info("DRY_RUN enabled - skipping YouTube upload.")

    # Record AFTER the video is on disk. Dry-run still counts for dedup so
    # repeated local test runs don't keep producing identical videos.
    history.record(wallet, run_id=run_id, video_id=youtube_video_id)

    return PipelineResult(
        run_dir=run_dir,
        wallet=wallet,
        script=script,
        video_path=video_path,
        metadata=metadata,
        youtube_video_id=youtube_video_id,
    )


def _open_history(settings: Settings):
    """Construct the HistoryStore using the configured lookback windows."""
    from src.utils.history import HistoryStore
    return HistoryStore(
        path=settings.history_file,
        wallet_lookback_days=settings.wallet_lookback_days,
        position_lookback_days=settings.position_lookback_days,
    )


def _check_ffmpeg(settings: Settings) -> None:
    if shutil.which("ffmpeg"):
        return
    raise RuntimeError(
        "ffmpeg not found on PATH. Install FFmpeg and ensure it's accessible.\n"
        "See: https://yt-plybt.readthedocs.io/#ffmpeg-setup"
    )


def _fetch_wallets(settings: Settings) -> list[WalletProfile]:
    from src.data_collection.wallet_validator import WalletValidator
    from src.filtering.wallet_filter import WalletFilter

    log.info("=== STAGE: Data Collection (source={}) ===", settings.data_source)

    if settings.data_source == "polymarket":
        wallets = _fetch_polymarket_wallets(settings)
    else:
        wallets = _fetch_dune_wallets(settings)

    log.info("=== STAGE: Validation ===")
    validator = WalletValidator()
    wallets = validator.validate(wallets)

    log.info("=== STAGE: Filtering ===")
    filter_cfg = WalletFilter.FilterConfig(
        min_roi_percent=settings.min_roi_percent,
        min_winrate=settings.min_winrate,
        min_trades=settings.min_trades,
        consistency_window_days=settings.consistency_window_days,
        max_wallets_per_run=settings.max_wallets_per_run,
    )
    wallet_filter = WalletFilter(filter_cfg)
    wallets = wallet_filter.apply(wallets)

    return wallets


def _fetch_dune_wallets(settings: Settings) -> list[WalletProfile]:
    from src.data_collection.dune_client import DuneWalletClient
    from src.data_collection.arkham_client import ArkhamClient

    dune = DuneWalletClient(settings.dune_api_key, settings.dune_wallet_query_id)
    wallets = dune.fetch()

    if settings.arkham_api_key:
        log.info("Enriching with Arkham...")
        arkham = ArkhamClient(settings.arkham_api_key, settings.arkham_base_url)
        wallets = arkham.enrich(wallets)
    else:
        log.warning("Arkham disabled - running on Dune data only.")
    return wallets


def _fetch_polymarket_wallets(settings: Settings) -> list[WalletProfile]:
    from src.data_collection.polymarket_client import PolymarketClient

    client = PolymarketClient(
        leaderboard_window=settings.polymarket_window,
        leaderboard_metric=settings.polymarket_metric,
        leaderboard_limit=settings.polymarket_leaderboard_limit,
        positions_per_user=settings.polymarket_positions_per_user,
        max_wallets_with_positions=settings.polymarket_max_wallets_with_positions,
    )
    return client.fetch()


def _select_wallet(wallets: list[WalletProfile], settings: Settings) -> Optional[WalletProfile]:
    if not wallets:
        return None
    return wallets[0]


def _generate_script(wallet: WalletProfile, settings: Settings, run_dir: Path) -> Script:
    log.info("=== STAGE: Script Generation ===")
    from src.content.script_generator import ScriptGenerator

    generator = ScriptGenerator(
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
    )
    return generator.generate(wallet, settings.target_video_seconds)


def _build_scenes(script: Script, settings: Settings, run_dir: Path):
    log.info("=== STAGE: Scene Breakdown ===")
    from src.content.scene_builder import SceneBuilder

    builder = SceneBuilder()
    return builder.build(script, settings.target_video_seconds, run_dir)


def _generate_images(scenes, settings: Settings, wallet: WalletProfile = None, run_dir: Path = None):
    from src.content.image_generator import ImageGenerator

    image_gen = ImageGenerator(
        provider=settings.image_provider,
        stability_api_key=settings.stability_api_key,
        leonardo_api_key=settings.leonardo_api_key,
        huggingface_api_key=settings.huggingface_api_key,
        openai_api_key=settings.openai_api_key,
    )
    wallet_data = asdict(wallet) if wallet else None

    if settings.image_provider == "none":
        log.info("Using placeholder images")
        for scene in scenes:
            scene.image_path = run_dir / "images" / f"scene_{scene.index:02d}.png"
            image_gen.generate(scene.on_screen_text, scene.image_path, scene.index, wallet_data)
    else:
        log.info("=== STAGE: Image Generation ===")
        from src.content.image_prompt_generator import ImagePromptGenerator

        prompt_gen = ImagePromptGenerator(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
        )
        for scene in scenes:
            scene.image_prompt = prompt_gen.enhance(scene.image_prompt, scene.on_screen_text)
            image_gen.generate(scene.image_prompt, scene.image_path, scene.index, wallet_data)


def _generate_narration(scenes, settings: Settings, run_dir: Path):
    log.info("=== STAGE: TTS Generation ===")
    from src.content.tts_generator import TTSGenerator
    from src.video.subtitle_generator import SubtitleGenerator

    tts = TTSGenerator(
        provider=settings.tts_provider,
        edge_voice=settings.edge_voice,
        elevenlabs_api_key=settings.elevenlabs_api_key,
        elevenlabs_voice_id=settings.elevenlabs_voice_id,
        elevenlabs_model=settings.elevenlabs_model,
        openai_api_key=settings.openai_api_key,
        openai_tts_voice=settings.openai_tts_voice,
    )

    for i, scene in enumerate(scenes):
        tts.synthesize(scene.narration, run_dir / f"narration_{i:02d}.mp3")

    # Overwrite each scene's predicted duration with the REAL TTS audio
    # length so the SRT cues line up with what the viewer actually hears.
    # Without this step the LLM-estimated duration drifts a few hundred ms
    # per scene and captions stop tracking the narration.
    for i, scene in enumerate(scenes):
        audio_path = run_dir / f"narration_{i:02d}.mp3"
        measured = _ffprobe_duration(audio_path)
        if measured is not None:
            log.debug(
                "Scene {}: predicted={:.2f}s -> measured={:.2f}s",
                i, scene.duration_sec, measured,
            )
            scene.duration_sec = measured

    # YouTube Shorts hard limit is 60s; anything longer falls out of the
    # Shorts shelf into the regular feed (≪ reach). If the sum of measured
    # narration exceeds our 58s safety cap, speed up every clip uniformly
    # via ffmpeg atempo so the pacing stays natural and captions still align.
    HARD_CAP_SEC = 58.0
    total = sum(s.duration_sec for s in scenes)
    if total > HARD_CAP_SEC:
        import shutil
        import subprocess
        speedup = total / HARD_CAP_SEC
        log.warning(
            "Total narration {:.1f}s exceeds {:.1f}s cap — applying atempo={:.3f}x to fit Shorts.",
            total, HARD_CAP_SEC, speedup,
        )
        if speedup > 2.0:
            log.warning("Speedup {:.2f}x > 2.0 — atempo chained to stay in valid range.", speedup)
        if shutil.which("ffmpeg"):
            for i, scene in enumerate(scenes):
                audio_path = run_dir / f"narration_{i:02d}.mp3"
                sped = audio_path.with_suffix(".sped.mp3")
                # atempo accepts 0.5–2.0 per filter; chain twice for >2x.
                if speedup <= 2.0:
                    afilter = f"atempo={speedup:.4f}"
                else:
                    half = speedup ** 0.5
                    afilter = f"atempo={half:.4f},atempo={half:.4f}"
                r = subprocess.run(
                    [
                        "ffmpeg", "-y", "-i", str(audio_path),
                        "-filter:a", afilter,
                        "-c:a", "libmp3lame", "-b:a", "192k",
                        str(sped),
                    ],
                    capture_output=True, text=True,
                )
                if r.returncode == 0 and sped.exists():
                    sped.replace(audio_path)
                    scene.duration_sec = scene.duration_sec / speedup
                else:
                    log.warning("atempo failed on scene {}: {}", i, r.stderr[-300:])

    sub_gen = SubtitleGenerator()
    sub_path = run_dir / "subtitles.srt"
    sub_gen.from_scenes(scenes, sub_path)


def _ffprobe_duration(path: Path) -> Optional[float]:
    """Return the duration of an audio file in seconds, or None on failure."""
    if not path.exists():
        return None
    import shutil
    import subprocess
    if not shutil.which("ffprobe"):
        log.warning("ffprobe not on PATH — falling back to predicted scene durations.")
        return None
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return None
        return float(result.stdout.strip())
    except (ValueError, subprocess.SubprocessError) as exc:
        log.warning("ffprobe failed on {}: {}", path.name, exc)
        return None


def _assemble_video(scenes, settings: Settings, run_dir: Path) -> Path:
    log.info("=== STAGE: Video Assembly ===")
    from src.video.ffmpeg_assembler import FFmpegAssembler

    assembler = FFmpegAssembler()
    output_path = run_dir / "video.mp4"
    
    narration_path = run_dir / "narration.mp3"
    subtitle_path = run_dir / "subtitles.srt"

    # Combine per-scene narration files into a single track. Re-encode rather
    # than `-c copy` because gtts/edge-tts can produce slightly different
    # bitrates that break stream copy.
    scene_audio_files = sorted(run_dir.glob("narration_*.mp3"))
    if scene_audio_files:
        import subprocess
        concat_file = run_dir / "audio_concat.txt"
        with open(concat_file, "w", encoding="utf-8") as f:
            for af in scene_audio_files:
                p = str(af.absolute()).replace("\\", "/")
                f.write(f"file '{p}'\n")

        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0",
                "-i", str(concat_file),
                "-vn",
                "-c:a", "libmp3lame", "-b:a", "192k",
                str(narration_path),
            ],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            log.warning("Audio concat failed:\n{}", result.stderr[-600:])
    
    # Only pass the SRT to the assembler when burn-in is desired. Otherwise
    # the SRT still exists on disk (used by caption upload), but ffmpeg won't
    # render it onto the frames — YouTube's CC system handles display.
    burn = getattr(settings, "burn_subtitles", False)
    assembler.assemble(
        scenes,
        output_path,
        narration_path=narration_path if narration_path.exists() else None,
        subtitle_path=subtitle_path if (burn and subtitle_path.exists()) else None,
        vertical=True
    )
    return output_path


def _generate_metadata(
    wallet: WalletProfile,
    script: Script,
    settings: Settings,
) -> VideoMetadata:
    log.info("=== STAGE: Metadata Generation ===")
    from src.youtube.metadata_generator import MetadataGenerator

    gen = MetadataGenerator(
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
    )
    return gen.generate(wallet, script)


def _translate_captions(run_dir: Path, settings: Settings) -> dict[str, Path]:
    """Build a {lang: srt_path} map for every configured caption language."""
    src_srt = run_dir / "subtitles.srt"
    if not src_srt.exists():
        log.warning("No source SRT at {} — skipping caption translation.", src_srt)
        return {}

    langs = [c.strip() for c in (settings.caption_languages or "").split(",") if c.strip()]
    if not langs:
        return {}

    source_lang = langs[0]
    log.info("=== STAGE: Caption Translation === source={}, targets={}",
             source_lang, langs)

    from src.video.srt_translator import translate_srt_to_many
    paths = translate_srt_to_many(src_srt, langs, source_lang=source_lang)
    return paths


def _upload(
    video_path: Path,
    metadata: VideoMetadata,
    settings: Settings,
    run_dir: Path,
    caption_paths: dict[str, Path] | None = None,
) -> str:
    log.info("=== STAGE: YouTube Upload ===")
    from src.youtube.uploader import YouTubeUploader

    uploader = YouTubeUploader(
        client_secrets_file=settings.youtube_client_secrets_file,
        token_file=settings.youtube_token_file,
    )
    video_id = uploader.upload(
        video_path=video_path,
        title=metadata.title,
        description=metadata.description,
        tags=metadata.tags,
        category_id=metadata.category_id,
        privacy_status=settings.youtube_privacy_status,
        made_for_kids=settings.youtube_made_for_kids,
    )

    if caption_paths:
        langs = [c.strip() for c in (settings.caption_languages or "").split(",") if c.strip()]
        source_lang = langs[0] if langs else "en"
        log.info("Uploading {} caption track(s)...", len(caption_paths))
        uploader.upload_captions(
            video_id=video_id,
            srt_paths=caption_paths,
            source_lang=source_lang,
        )

    return video_id