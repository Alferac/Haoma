from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from config import Settings
from services import file_writer, llm, youtube

router = Router()

_YT_VIDEO_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com/(?:watch\?[^\s]*v=|shorts/|embed/|v/)|youtu\.be/)[a-zA-Z0-9_?=&%\-]+"
)
_YT_CHANNEL_URL_RE = re.compile(
    r"https?://(?:www\.)?youtube\.com/(@[\w\-.]+|channel/[\w\-]+|c/[\w\-.]+|user/[\w\-.]+)(?:/[\w\-]*)?\S*"
)


def _extract_video_url(text: str) -> str | None:
    match = _YT_VIDEO_URL_RE.search(text)
    return match.group(0) if match else None


def _extract_channel_url(text: str) -> str | None:
    match = _YT_CHANNEL_URL_RE.search(text)
    return match.group(0) if match else None


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Привет! Отправь мне:\n"
        "• Ссылку на <b>видео</b> — получишь конспект\n"
        "• Ссылку на <b>канал</b> — обработаю последние N видео\n\n"
        "Конспект сохранится в папку Obsidian."
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "Поддерживаемые ссылки:\n\n"
        "<b>Видео:</b>\n"
        "• https://www.youtube.com/watch?v=...\n"
        "• https://youtu.be/...\n"
        "• https://youtube.com/shorts/...\n\n"
        "<b>Каналы:</b>\n"
        "• https://www.youtube.com/@channelname\n"
        "• https://www.youtube.com/channel/UCxxxxxx\n"
        "• https://www.youtube.com/c/channelname"
    )


async def _process_single_video(
    url: str,
    settings: Settings,
) -> tuple[str | None, Path | None, str | None]:
    """
    Process one video: transcript → LLM → save.
    Returns (title, filepath, error_message).
    """
    try:
        result = await youtube.get_transcript(
            url=url,
            languages=settings.subtitles.languages,
            prefer_manual=settings.subtitles.prefer_manual,
            proxies=settings.proxy.as_dict() or None,
        )
    except ValueError as e:
        return None, None, str(e)

    existing_path = file_writer.get_note_path(result.title, settings.output)
    if existing_path.exists():
        log.info("SKIP already exists: %s", result.title)
        return result.title, existing_path, None

    try:
        analysis = await llm.analyze_transcript(
            transcript=result.text,
            title=result.title,
            url=url,
            settings=settings.llm,
            anthropic_api_key=settings.anthropic_api_key,
            openrouter_api_key=settings.openrouter_api_key,
            channel_name=result.channel_name,
        )
    except RuntimeError as e:
        log.error("LLM error [%s]: %s", result.title, e)
        return result.title, None, str(e)

    try:
        filepath = await file_writer.save_note(
            title=result.title,
            url=url,
            analysis=analysis,
            language=result.language,
            model=settings.llm.model,
            provider=settings.llm.provider,
            settings=settings.output,
            transcript_text=result.text,
            prompt_version=settings.llm.prompt_version,
            temperature=settings.llm.temperature,
        )
    except Exception as e:
        log.error("Save error [%s]: %s", result.title, e)
        return result.title, None, str(e)

    log.info("SAVED: %s", result.title)
    return result.title, filepath, None


@router.message()
async def handle_message(message: Message, settings: Settings) -> None:
    text = message.text or message.caption or ""

    # --- Channel URL ---
    channel_url = _extract_channel_url(text)
    if channel_url and youtube.is_channel_url(channel_url):
        await _handle_channel(message, channel_url, settings)
        return

    # --- Single video URL ---
    video_url = _extract_video_url(text)
    if video_url:
        await _handle_video(message, video_url, settings)
        return

    await message.answer(
        "Не вижу YouTube-ссылки в сообщении.\n"
        "Отправь ссылку на видео или канал."
    )


async def _handle_video(message: Message, url: str, settings: Settings) -> None:
    status_msg = await message.answer("⏳ Извлекаю субтитры...")

    try:
        result = await youtube.get_transcript(
            url=url,
            languages=settings.subtitles.languages,
            prefer_manual=settings.subtitles.prefer_manual,
            proxies=settings.proxy.as_dict() or None,
        )
    except ValueError as e:
        await status_msg.edit_text(f"❌ Ошибка при извлечении субтитров:\n{e}")
        return

    existing_path = file_writer.get_note_path(result.title, settings.output)
    if existing_path.exists():
        await status_msg.edit_text(
            f"⚠️ Конспект уже существует:\n<code>{existing_path}</code>"
        )
        return

    sub_type = "ручные" if not result.is_generated else "авто"
    await status_msg.edit_text(
        f"🤖 Субтитры получены ({result.language}, {sub_type}).\n"
        f"Анализирую с помощью ИИ..."
    )

    try:
        analysis = await llm.analyze_transcript(
            transcript=result.text,
            title=result.title,
            url=url,
            settings=settings.llm,
            anthropic_api_key=settings.anthropic_api_key,
            openrouter_api_key=settings.openrouter_api_key,
            channel_name=result.channel_name,
        )
    except RuntimeError as e:
        await status_msg.edit_text(f"❌ Ошибка при анализе:\n{e}")
        return

    await status_msg.edit_text("💾 Сохраняю конспект...")

    try:
        filepath = await file_writer.save_note(
            title=result.title,
            url=url,
            analysis=analysis,
            language=result.language,
            model=settings.llm.model,
            provider=settings.llm.provider,
            settings=settings.output,
            transcript_text=result.text,
            prompt_version=settings.llm.prompt_version,
            temperature=settings.llm.temperature,
        )
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка при сохранении файла:\n{e}")
        return

    preview = analysis[:3000]
    if len(analysis) > 3000:
        preview += "\n\n<i>...конспект обрезан для предпросмотра...</i>"

    await status_msg.delete()
    await message.answer(
        f"<b>{result.title}</b>\n\n"
        f"{preview}\n\n"
        f"─────────────────\n"
        f"💾 Файл: <code>{filepath}</code>",
    )


async def _handle_channel(message: Message, channel_url: str, settings: Settings) -> None:
    max_n = settings.channel.max_videos
    status_msg = await message.answer(
        f"📋 Получаю список видео из канала (до {max_n} шт.)..."
    )

    try:
        video_urls = await youtube.get_channel_video_urls(
            channel_url, max_n, proxies=settings.proxy.as_dict() or None
        )
    except ValueError as e:
        await status_msg.edit_text(f"❌ {e}")
        return

    total = len(video_urls)
    await status_msg.edit_text(f"🎬 Найдено видео: {total}. Начинаю обработку...")

    saved: list[tuple[str, Path]] = []   # (title, filepath)
    failed: list[tuple[str, str]] = []   # (url_or_title, reason)

    for idx, video_url in enumerate(video_urls, start=1):
        try:
            await status_msg.edit_text(
                f"⏳ Видео {idx}/{total}: обрабатываю...\n"
                f"✅ Готово: {len(saved)}  ❌ Пропущено: {len(failed)}"
            )
        except Exception:
            pass

        title, filepath, error = await _process_single_video(video_url, settings)

        if error:
            label = title or video_url
            failed.append((label, error))
        else:
            saved.append((title, filepath))

    try:
        await status_msg.edit_text(
            f"✅ Канал обработан. Видео: {total}\n"
            f"Сохранено: {len(saved)}   Пропущено: {len(failed)}"
        )
    except Exception:
        pass
