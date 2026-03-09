from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

import httpx
import scrapetube
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    TranscriptsDisabled,
    VideoUnavailable,
)

# Instantiated per-call with optional proxy (see get_transcript / get_channel_video_urls)

# All known YouTube video URL patterns
_YT_PATTERNS = [
    r"(?:youtube\.com/watch\?(?:.*&)?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/v/|youtube\.com/shorts/)([a-zA-Z0-9_-]{11})",
]

# YouTube channel URL pattern (handles @handle, /channel/ID, /c/name, /user/name)
_YT_CHANNEL_RE = re.compile(
    r"https?://(?:www\.)?youtube\.com/(@[\w\-.]+|channel/[\w\-]+|c/[\w\-.]+|user/[\w\-.]+)(?:/[\w\-]*)?(?:\?[^\s]*)?\s*$"
)


@dataclass
class TranscriptResult:
    video_id: str
    title: str
    text: str
    language: str
    is_generated: bool


def extract_video_id(url: str) -> str | None:
    for pattern in _YT_PATTERNS:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def is_channel_url(url: str) -> bool:
    """Return True if the URL points to a YouTube channel rather than a specific video."""
    return bool(_YT_CHANNEL_RE.match(url.strip()))


async def get_channel_video_urls(channel_url: str, max_videos: int, proxies: dict | None = None) -> list[str]:
    """
    Return up to `max_videos` video URLs from a YouTube channel (newest first).

    Uses scrapetube which talks to YouTube's internal API — no API key needed.

    Raises:
        ValueError: If no videos could be found or the channel doesn't exist.
    """
    loop = asyncio.get_event_loop()

    def _fetch() -> list[str]:
        import os
        if proxies:
            os.environ.setdefault("HTTP_PROXY", proxies.get("http", ""))
            os.environ.setdefault("HTTPS_PROXY", proxies.get("https", ""))
        videos = scrapetube.get_channel(
            channel_url=channel_url.strip(),
            limit=max_videos,
            sort_by="newest",
            content_type_filters=["video"],
        )
        return [
            f"https://www.youtube.com/watch?v={v['videoId']}"
            for v in videos
        ]

    try:
        urls = await loop.run_in_executor(None, _fetch)
    except Exception as e:
        raise ValueError(f"Не удалось получить список видео из канала: {e}")

    if not urls:
        raise ValueError("На канале не найдено видео (или канал не существует).")

    return urls


async def fetch_video_title(video_id: str, proxies: dict | None = None) -> str:
    """Fetch video title from YouTube page without yt-dlp."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        proxy_url = (proxies or {}).get("https") or (proxies or {}).get("http") or None
        async with httpx.AsyncClient(timeout=10, follow_redirects=True, proxy=proxy_url) as client:
            response = await client.get(
                url,
                headers={"Accept-Language": "ru,en;q=0.9"},
            )
            response.raise_for_status()
            html = response.text
            # Try og:title meta tag first (most reliable)
            match = re.search(r'<meta property="og:title" content="([^"]+)"', html)
            if match:
                return match.group(1)
            # Fallback: <title> tag
            match = re.search(r"<title>([^<]+)</title>", html)
            if match:
                title = match.group(1)
                return re.sub(r"\s*[-–]\s*YouTube\s*$", "", title).strip()
    except Exception:
        pass
    return video_id


def _pick_transcript(transcripts: list, languages: list[str], prefer_manual: bool) -> object | None:
    """Select the best available transcript based on language priority and manual preference."""
    manual = [t for t in transcripts if not t.is_generated]
    generated = [t for t in transcripts if t.is_generated]

    def find_by_lang(pool: list, langs: list[str]) -> object | None:
        for lang in langs:
            for t in pool:
                if t.language_code.startswith(lang):
                    return t
        return None

    if prefer_manual:
        result = find_by_lang(manual, languages) or find_by_lang(generated, languages)
    else:
        result = find_by_lang(generated, languages) or find_by_lang(manual, languages)

    if result:
        return result

    # Final fallback: first available transcript
    all_transcripts = (manual + generated) if prefer_manual else (generated + manual)
    return all_transcripts[0] if all_transcripts else None


def _segment_text(seg) -> str:
    """Extract text from a segment — handles both dict (v0.x) and object (v1.x) formats."""
    if isinstance(seg, dict):
        return seg.get("text", "")
    return getattr(seg, "text", "")


async def get_transcript(
    url: str,
    languages: list[str],
    prefer_manual: bool,
    proxies: dict | None = None,
) -> TranscriptResult:
    """
    Extract transcript from YouTube video.

    Raises:
        ValueError: If URL is invalid, video has no subtitles, etc.
    """
    video_id = extract_video_id(url)
    if not video_id:
        raise ValueError("Не удалось извлечь ID видео из ссылки. Убедитесь, что это корректная YouTube-ссылка.")

    api = YouTubeTranscriptApi(proxies=proxies) if proxies else YouTubeTranscriptApi()
    try:
        transcript_list = api.list(video_id)
    except VideoUnavailable:
        raise ValueError("Видео недоступно или не существует.")
    except TranscriptsDisabled:
        raise ValueError("Субтитры для этого видео отключены.")
    except Exception as e:
        raise ValueError(f"Не удалось получить список субтитров: {e}")

    transcript_obj = _pick_transcript(list(transcript_list), languages, prefer_manual)
    if transcript_obj is None:
        raise ValueError("Субтитры для этого видео не найдены.")

    try:
        segments = transcript_obj.fetch()
    except Exception as e:
        raise ValueError(f"Не удалось загрузить субтитры: {e}")

    text_parts = []
    for seg in segments:
        part = _segment_text(seg).replace("\n", " ").strip()
        if part:
            text_parts.append(part)
    full_text = " ".join(text_parts)

    title = await fetch_video_title(video_id, proxies=proxies)

    return TranscriptResult(
        video_id=video_id,
        title=title,
        text=full_text,
        language=transcript_obj.language_code,
        is_generated=transcript_obj.is_generated,
    )
