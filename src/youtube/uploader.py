"""Upload videos to YouTube using Google API.

Handles OAuth flow, video upload with metadata, and caption-track upload
in additional languages so YouTube renders a real CC button + language
picker on the player.

Uses YouTube Data API v3.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from src.utils.logger import log


# `youtube.force-ssl` is required for captions.insert. It also covers
# uploads, so this single scope replaces the old upload-only one. NOTE:
# bumping the scope INVALIDATES any existing token.json — the next run
# will re-prompt OAuth (one-time).
SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
API_SERVICE_NAME = "youtube"
API_VERSION = "v3"


class YouTubeUploader:
    def __init__(
        self,
        client_secrets_file: Path,
        token_file: Path,
    ):
        self._secrets = client_secrets_file
        self._token = token_file
        self._client = None

    def upload(
        self,
        video_path: Path,
        title: str,
        description: str,
        tags: List[str],
        category_id: str = "28",
        privacy_status: str = "private",
        made_for_kids: bool = False,
    ) -> str:
        if not self._secrets.exists():
            raise FileNotFoundError(f"Client secrets not found: {self._secrets}")

        credentials = self._get_credentials()
        self._client = build(API_SERVICE_NAME, API_VERSION, credentials=credentials)

        body = {
            "snippet": {
                "title": title[:100],
                "description": description[:5000],
                "tags": tags[:15],
                "categoryId": category_id,
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": made_for_kids,
            },
        }

        try:
            media = MediaFileUpload(
                str(video_path),
                chunksize=-1,           # one chunk for files < ~128 MB (Shorts fit easily)
                resumable=True,
                mimetype="video/mp4",
            )

            request = self._client.videos().insert(
                part="snippet,status",
                body=body,
                media_body=media,
            )

            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    log.info("Upload progress: {:.1f}%", status.progress() * 100)

            if not response or "id" not in response:
                raise RuntimeError(f"YouTube returned no video id: {response!r}")

            video_id = response["id"]
            log.info("Uploaded video id: {}  (https://youtu.be/{})", video_id, video_id)
            return video_id

        except HttpError as exc:
            log.error("YouTube API error: {}", exc)
            raise

    # ------------------------------------------------------------------
    # Captions
    # ------------------------------------------------------------------
    def upload_captions(
        self,
        video_id: str,
        srt_paths: Dict[str, Path],
        source_lang: str = "en",
    ) -> Dict[str, str]:
        """Upload one caption track per language.

        Parameters
        ----------
        video_id
            The newly-created video.
        srt_paths
            Mapping of language code -> path to .srt file.
        source_lang
            The lang considered "original" — gets ``isDraft=False`` and a
            blank/explicit name. Other tracks get a "Spanish" / "Portuguese"
            style label so the player picker reads cleanly.
        """
        if not srt_paths:
            log.info("No caption tracks to upload.")
            return {}

        if self._client is None:
            credentials = self._get_credentials()
            self._client = build(
                API_SERVICE_NAME, API_VERSION, credentials=credentials,
            )

        results: Dict[str, str] = {}
        for lang, srt_path in srt_paths.items():
            if not srt_path.exists():
                log.warning("Caption file missing for {}: {}", lang, srt_path)
                continue
            try:
                caption_id = self._insert_caption(
                    video_id=video_id,
                    lang=lang,
                    srt_path=srt_path,
                    is_draft=False,
                    name=_LANG_LABELS.get(lang, lang.upper()),
                )
                results[lang] = caption_id
                log.info("Caption [{}] uploaded id={}", lang, caption_id)
            except HttpError as exc:
                log.error("Caption upload failed for {}: {}", lang, exc)
            except Exception as exc:  # noqa: BLE001
                log.error("Caption upload errored for {}: {}", lang, exc)

        return results

    def _insert_caption(
        self,
        video_id: str,
        lang: str,
        srt_path: Path,
        is_draft: bool,
        name: str,
    ) -> str:
        body = {
            "snippet": {
                "videoId": video_id,
                "language": lang,
                "name": name,
                "isDraft": is_draft,
            },
        }
        media = MediaFileUpload(
            str(srt_path),
            mimetype="application/octet-stream",
            resumable=False,
        )
        response = self._client.captions().insert(
            part="snippet",
            body=body,
            media_body=media,
            sync=False,
        ).execute()
        return response["id"]

    def _get_credentials(self) -> Credentials:
        if self._token.exists():
            creds = Credentials.from_authorized_user_info(
                json.loads(self._token.read_text()),
                SCOPES,
            )
            if creds and creds.valid:
                return creds
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                self._save_credentials(creds)
                return creds

        flow = InstalledAppFlow.from_client_secrets_file(
            str(self._secrets), SCOPES
        )
        creds = flow.run_local_server(port=0)
        self._save_credentials(creds)
        return creds

    def _save_credentials(self, creds: Credentials):
        self._token.parent.mkdir(parents=True, exist_ok=True)
        self._token.write_text(
            json.dumps(
                {
                    "token": creds.token,
                    "refresh_token": creds.refresh_token,
                    "token_uri": creds.token_uri,
                    "client_id": creds.client_id,
                    "client_secret": creds.client_secret,
                    "scopes": creds.scopes,
                }
            )
        )
        log.info("Credentials saved to {}", self._token)


# Friendly labels shown in the YouTube player's caption picker. Keys are
# ISO 639-1 language codes (the same codes accepted by captions.insert).
_LANG_LABELS: Dict[str, str] = {
    "en": "English",
    "es": "Español",
    "pt": "Português",
    "hi": "हिन्दी",
    "id": "Bahasa Indonesia",
    "tr": "Türkçe",
    "fr": "Français",
    "de": "Deutsch",
    "ja": "日本語",
    "ko": "한국어",
    "ar": "العربية",
    "ru": "Русский",
    "vi": "Tiếng Việt",
    "zh": "中文",
}