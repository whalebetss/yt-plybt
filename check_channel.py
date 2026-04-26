"""Print which YouTube channel the OAuth token is bound to."""
from pathlib import Path
from googleapiclient.discovery import build
from config.settings import get_settings
from src.youtube.uploader import API_SERVICE_NAME, API_VERSION, YouTubeUploader

s = get_settings()
u = YouTubeUploader(
    client_secrets_file=Path(s.youtube_client_secrets_file),
    token_file=Path(s.youtube_token_file),
)
yt = build(API_SERVICE_NAME, API_VERSION, credentials=u._get_credentials())
r = yt.channels().list(part="snippet,statistics", mine=True).execute()
for c in r.get("items", []):
    sn = c["snippet"]
    st = c["statistics"]
    print(f"Channel: {sn['title']}")
    print(f"Handle:  @{sn.get('customUrl','?')}")
    print(f"ID:      {c['id']}")
    print(f"Subs:    {st.get('subscriberCount','?')}")
    print(f"Videos:  {st.get('videoCount','?')}")
    print(f"URL:     https://www.youtube.com/channel/{c['id']}")
