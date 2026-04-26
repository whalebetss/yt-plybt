"""Generate TTS narration audio using edge-tts (free, many voices)."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Literal

import aiohttp
import requests

from src.utils.logger import log


class TTSGenerator:
    PROVIDERS = Literal["edge", "gtts", "elevenlabs", "openai"]

    def __init__(
        self,
        provider: PROVIDERS = "edge",
        elevenlabs_api_key: str = "",
        elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM",
        elevenlabs_model: str = "eleven_turbo_v2_5",
        openai_api_key: str = "",
        openai_tts_voice: str = "onyx",
        edge_voice: str = "en-US-JasonNeural",
    ):
        self._provider = provider
        self._eleven_key = elevenlabs_api_key
        self._eleven_voice = elevenlabs_voice_id
        self._eleven_model = elevenlabs_model
        self._openai_key = openai_api_key
        self._openai_voice = openai_tts_voice
        self._edge_voice = edge_voice

    def synthesize(self, text: str, output_path: Path) -> bool:
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if self._provider == "edge":
            return self._synthesize_edge(text, output_path)
        elif self._provider == "gtts":
            return self._synthesize_gtts(text, output_path)
        elif self._provider == "elevenlabs":
            return self._synthesize_eleven(text, output_path)
        elif self._provider == "openai":
            return self._synthesize_openai(text, output_path)
        else:
            log.error("Unknown TTS provider: {}", self._provider)
            return False

    def _synthesize_edge(self, text: str, output_path: Path) -> bool:
        import edge_tts
        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            async def go():
                communicate = edge_tts.Communicate(text, self._edge_voice)
                await communicate.save(str(output_path))
            
            asyncio.run(go())
            log.info("Edge TTS saved to {} ({})", output_path, self._edge_voice)
            return True
        except Exception as exc:
            log.error("Edge TTS failed: {}", exc)
            return self._synthesize_gtts(text, output_path)

    def _synthesize_gtts(self, text: str, output_path: Path) -> bool:
        try:
            from gtts import gTTS
            tts = gTTS(text=text, lang="en", slow=False)
            tts.save(str(output_path))
            log.info("gTTS saved to {}", output_path)
            return True
        except Exception as exc:
            log.error("gTTS failed: {}", exc)
            return self._create_silence(output_path)

    def _synthesize_eleven(self, text: str, output_path: Path) -> bool:
        if not self._eleven_key:
            return self._synthesize_edge(text, output_path)

        try:
            resp = requests.post(
                f"https://api.elevenlabs.io/v1/text_to_audio/{self._eleven_voice}",
                headers={"Accept": "audio/mpeg", "Content-Type": "application/json", "xi-api-key": self._eleven_key},
                json={"text": text, "model_id": self._eleven_model, "voice_settings": {"stability": 0.5, "similarity_boost": 0.8}},
                timeout=60,
            )
            if resp.status_code != 200:
                return self._synthesize_edge(text, output_path)
            output_path.write_bytes(resp.content)
            log.info("ElevenLabs TTS saved to {}", output_path)
            return True
        except Exception as exc:
            log.error("ElevenLabs failed: {}, using edge", exc)
            return self._synthesize_edge(text, output_path)

    def _synthesize_openai(self, text: str, output_path: Path) -> bool:
        if not self._openai_key:
            return self._synthesize_edge(text, output_path)

        try:
            from openai import OpenAI
            client = OpenAI(api_key=self._openai_key)
            response = client.audio.speech.create(model="tts-1", voice=self._openai_voice, input=text, response_format="mp3")
            output_path.write_bytes(response.content)
            log.info("OpenAI TTS saved to {}", output_path)
            return True
        except Exception as exc:
            log.error("OpenAI failed: {}, using edge", exc)
            return self._synthesize_edge(text, output_path)

    def _create_silence(self, output_path: Path) -> bool:
        import io
        import wave
        output_path.parent.mkdir(parents=True, exist_ok=True)
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(44100)
            w.writeframes(b"\x00" * 88200)
        output_path.write_bytes(buffer.getvalue())
        return True