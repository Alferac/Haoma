from __future__ import annotations

import re
import subprocess
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import aiofiles

from config import OutputSettings


_FORBIDDEN_CHARS = re.compile(r"[\\/:*?\"<>|]")
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _sanitize_filename(name: str, max_length: int) -> str:
    sanitized = _FORBIDDEN_CHARS.sub("", name)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    sanitized = sanitized[:max_length]
    return sanitized or "untitled"


def _build_text_label(safe_title: str) -> str:
    return f"Text: {safe_title}"


@lru_cache(maxsize=1)
def _get_haoma_version() -> str:
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        version = result.stdout.strip()
        if version:
            return version
    except (subprocess.CalledProcessError, FileNotFoundError):  # pragma: no cover - best effort
        pass
    return "unknown"


def _build_frontmatter(
    title: str,
    url: str,
    language: str,
    model: str,
    provider: str,
    text_link: str,
    haoma_version: str,
    prompt_version: str,
    temperature: float,
) -> str:
    now = datetime.now()
    return (
        "---\n"
        f'title: "{title}"\n'
        f"url: {url}\n"
        f"date: {now.strftime('%Y-%m-%d')}\n"
        f"source: youtube\n"
        f"language: {language}\n"
        f"model: {model}\n"
        f"provider: {provider}\n"
        f"Temp: {temperature}\n"
        f"Prompt ver: {prompt_version}\n"
        f"Haoma ver: {haoma_version}\n"
        f"Text: {text_link}\n"
        "index: true\n"
        "Human: false\n"
        "tags:\n"
        "  - youtube\n"
        "  - summary\n"
        "---\n\n"
    )


def get_note_path(title: str, settings: OutputSettings) -> Path:
    """Return the expected output path for a note without creating it."""
    safe_title = _sanitize_filename(title, settings.max_filename_length)
    return settings.folder / f"{safe_title}.md"


async def save_note(
    title: str,
    url: str,
    analysis: str,
    language: str,
    model: str,
    provider: str,
    settings: OutputSettings,
    transcript_text: str,
    prompt_version: str = "",
    temperature: float = 0.0,
) -> Path:
    safe_title = _sanitize_filename(title, settings.max_filename_length)
    note_path = settings.folder / f"{safe_title}.md"

    text_label = _build_text_label(safe_title)
    text_filename = _sanitize_filename(text_label.replace(":", "："), settings.max_filename_length)
    text_path = settings.folder / f"{text_filename}.md"
    text_link = f'"[[{text_filename}]]"'

    haoma_version = _get_haoma_version()

    content_parts: list[str] = []
    if settings.add_frontmatter:
        content_parts.append(
            _build_frontmatter(
                title,
                url,
                language,
                model,
                provider,
                text_link,
                haoma_version,
                prompt_version,
                temperature,
            )
        )

    content_parts.append(analysis)
    content_parts.append("\n")

    content = "".join(content_parts)

    async with aiofiles.open(note_path, "w", encoding="utf-8") as f:
        await f.write(content)

    async with aiofiles.open(text_path, "w", encoding="utf-8") as tf:
        await tf.write(transcript_text)

    return note_path
