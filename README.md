# Crypto Wallet Shorts — Autonomous Pipeline

End-to-end pipeline that pulls high-performing on-chain wallets from Dune Analytics + Arkham, validates the data, writes a short script, generates narration and visuals, assembles a vertical 9:16 video with FFmpeg, and uploads it to YouTube on a schedule.

## Disclaimer (read first)

This project produces **educational/entertainment** content about publicly observable on-chain activity. It is **not financial advice**. Past wallet performance does not predict future results. The pipeline:

- Refuses to generate "guaranteed return" / "you'll get rich" language.
- Burns a disclaimer overlay into every video.
- Appends a disclaimer block to every YouTube description.
- Defaults uploads to `private` — flip to `public` only after human review.

Use only for jurisdictions and audiences where this kind of educational commentary is permitted.

## Pipeline

```
Dune ─┐
      ├─► validate ─► filter ─► script ─► scenes ─► image prompts
Arkham┘                                  └► TTS ───────┐
                                                        ▼
                                            FFmpeg assembly (9:16, subs)
                                                        ▼
                                            YouTube metadata + upload
```

## Setup

### 1. System dependencies

- Python 3.11+
- FFmpeg on PATH (`ffmpeg -version` must work)
- A Google Cloud project with **YouTube Data API v3** enabled and an OAuth client (Desktop app).

### 2. Install

```bash
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows
.venv\Scripts\activate

pip install -r requirements.txt
```

### 3. Credentials

```bash
cp .env.example .env
# Fill in API keys in .env

mkdir credentials
# Put your downloaded YouTube OAuth file at:
#   credentials/client_secret.json
```

The first upload will open a browser window for OAuth and cache a refresh token at `credentials/token.json`.

### 4. Smoke test (no upload)

```bash
# In .env set DRY_RUN=true
python main.py --once
```

Outputs land in `./output/<run-id>/`:

```
output/2026-04-23T14-00-00/
├── wallets.json
├── script.json
├── scenes.json
├── narration.mp3
├── subtitles.srt
├── images/
├── video.mp4
└── metadata.json
```

### 5. Run on a schedule

```bash
python scheduler.py
```

Cron expression is read from `RUN_SCHEDULE_CRON` in `.env` (default daily at 14:00).

## Project structure

```
YT-PLYBT/
├── main.py                  # one-shot pipeline entrypoint
├── scheduler.py             # APScheduler loop using RUN_SCHEDULE_CRON
├── requirements.txt
├── .env.example
├── README.md
├── config/
│   └── settings.py          # pydantic-settings, validates .env
└── src/
    ├── pipeline.py          # orchestrates the end-to-end run
    ├── data_collection/
    │   ├── dune_client.py
    │   ├── arkham_client.py
    │   └── wallet_validator.py
    ├── filtering/
    │   └── wallet_filter.py
    ├── content/
    │   ├── script_generator.py
    │   ├── scene_builder.py
    │   ├── image_prompt_generator.py
    │   ├── image_generator.py
    │   └── tts_generator.py
    ├── video/
    │   ├── subtitle_generator.py
    │   └── ffmpeg_assembler.py
    ├── youtube/
    │   ├── metadata_generator.py
    │   └── uploader.py
    └── utils/
        ├── models.py        # shared dataclasses
        ├── logger.py        # loguru setup
        └── disclaimers.py
```

## Compliance notes

- `INCLUDE_DISCLAIMER=true` (default) is enforced — the pipeline refuses to render a video without it.
- The script generator's system prompt forbids: guarantees, "buy now" CTAs, recommended trades, price predictions.
- Wallet addresses are publicly visible on-chain. The pipeline never tries to dox real-world identities; if Arkham labels are present they're used as displayed (e.g. "Wintermute"), and unlabeled wallets stay anonymous as truncated addresses.

## Common operational issues

| Symptom | Likely cause |
|---|---|
| `ffmpeg: command not found` | FFmpeg not installed or not on PATH |
| Dune query returns empty | Your `DUNE_WALLET_QUERY_ID` query hasn't run recently — execute it once in Dune UI |
| Arkham 401 | API key not whitelisted; pipeline will fall back to Dune-only with a warning |
| YouTube upload `quotaExceeded` | Daily upload quota is 6 videos/day on the default project; request a quota bump |
| Video has no audio | TTS provider rejected the script — check `output/<run-id>/narration.log` |

## License

MIT.
