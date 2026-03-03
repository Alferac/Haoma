from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import aiofiles

from config import OutputSettings


_FORBIDDEN_CHARS = re.compile(r'[\\/:*?"<>|]')


def _sanitize_filename(name: str, max_length: int) -> str:
    sanitized = _FORBIDDEN_CHARS.sub("", name)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    sanitized = sanitized[:max_length]
    return sanitized or "untitled"


def _build_frontmatter(title: str, url: str, language: str, model: str, provider: str) -> str:
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
        "tags:\n"
        "  - youtube\n"
        "  - summary\n"
        "---\n\n"
    )


async def save_note(
    title: str,
    url: str,
    analysis: str,
    language: str,
    model: str,
    provider: str,
    settings: OutputSettings,
) -> Path:
    """
    Save LLM analysis as a Markdown note in the output folder.

    Returns the path to the saved file.
    """
    date_str = datetime.now().strftime("%Y-%m-%d")
    safe_title = _sanitize_filename(title, settings.max_filename_length)
    filename = f"{date_str} - {safe_title}.md"
    filepath = settings.folder / filename

    content_parts = []

    if settings.add_frontmatter:
        content_parts.append(_build_frontmatter(title, url, language, model, provider))

    content_parts.append(f"# {title}\n\n")
    content_parts.append(f"> **Источник:** [{url}]({url})\n\n")
    content_parts.append(analysis)
    content_parts.append("\n")

    content = "".join(content_parts)

    async with aiofiles.open(filepath, "w", encoding="utf-8") as f:
        await f.write(content)

    return filepath
