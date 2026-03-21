from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import datetime
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from config import Settings
from reconciler import build_plan, extract_entities_yaml, load_index, save_index
from services.generator import apply_plan_with_llm
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


def _parse_frontmatter(content: str) -> dict:
    """Извлекает YAML frontmatter из markdown-файла."""
    match = re.match(r"^---\n(.+?)\n---", content, re.DOTALL)
    if not match:
        return {}
    try:
        return yaml.safe_load(match.group(1)) or {}
    except Exception:
        return {}


def _set_index_true(filepath: Path) -> None:
    """Проставляет index: true в frontmatter файла."""
    content = filepath.read_text(encoding="utf-8")
    if re.search(r"^index:", content, re.MULTILINE):
        content = re.sub(r"^index:.*$", "index: true", content, flags=re.MULTILINE)
    else:
        content = re.sub(r"\n---\n", "\nindex: true\n---\n", content, count=1)
    filepath.write_text(content, encoding="utf-8")


@router.message(Command("reindex"))
async def cmd_reindex(message: Message, settings: Settings) -> None:
    source_folder = settings.output.folder
    all_md = sorted(source_folder.glob("*.md"))

    to_process: list[tuple[Path, str, dict]] = []
    for f in all_md:
        if f.name.startswith("Text"):
            continue
        content = f.read_text(encoding="utf-8")
        fm = _parse_frontmatter(content)
        if fm.get("index") is True:
            continue
        to_process.append((f, content, fm))

    total = len(to_process)
    if not total:
        await message.answer("✅ Все статьи уже проиндексированы.")
        return

    status_msg = await message.answer(
        f"🔄 Найдено статей для индексации: <b>{total}</b>. Начинаю..."
    )

    done = 0
    errors = 0

    for filepath, content, fm in to_process:
        title = str(fm.get("title", filepath.stem))
        url = str(fm.get("url", ""))
        date = str(fm.get("date", ""))
        source_info_date = date or datetime.now().strftime("%Y-%m-%d")

        try:
            await _run_reconciler(content, title, url, "", str(filepath), settings)
            _set_index_true(filepath)
            done += 1
        except Exception as exc:
            log.error("Reindex error [%s]: %s", title, exc)
            errors += 1

        processed = done + errors
        if processed % 5 == 0 or processed == total:
            try:
                await status_msg.edit_text(
                    f"🔄 Индексация: {processed}/{total}\n"
                    f"✅ Готово: {done}  ❌ Ошибок: {errors}"
                )
            except Exception:
                pass

    await status_msg.edit_text(
        f"✅ Индексация завершена.\n"
        f"Обработано: {done}  ❌ Ошибок: {errors}"
    )


def _set_enrich_true(filepath: Path) -> None:
    """Проставляет enrich: true в frontmatter файла."""
    content = filepath.read_text(encoding="utf-8")
    content = re.sub(r"^enrich:.*$", "enrich: true", content, flags=re.MULTILINE)
    filepath.write_text(content, encoding="utf-8")


@router.message(Command("enrich"))
async def cmd_enrich(message: Message, settings: Settings) -> None:
    if not settings.enrich.prompt:
        await message.answer("❌ Промт обогащения не настроен (enrich.prompt_file в config.yaml).")
        return

    vault = Path(settings.reconciler.vault_path)
    if not vault.exists():
        await message.answer("❌ Vault не найден. Проверь reconciler.vault_path в config.yaml.")
        return

    source_folder = settings.output.folder.resolve()

    to_process: list[Path] = []
    for f in sorted(vault.rglob("*.md")):
        if f.name.startswith("Text"):
            continue
        try:
            f.resolve().relative_to(source_folder)
            continue  # файл внутри source — пропускаем
        except ValueError:
            pass
        content = f.read_text(encoding="utf-8")
        fm = _parse_frontmatter(content)
        if fm.get("enrich") is not False:
            continue
        to_process.append(f)

    total = len(to_process)
    if not total:
        await message.answer("✅ Нет заметок с enrich: false.")
        return

    status_msg = await message.answer(
        f"✨ Найдено заметок для обогащения: <b>{total}</b>. Начинаю..."
    )

    done = 0
    errors = 0

    for filepath in to_process:
        current_content = filepath.read_text(encoding="utf-8")
        fm = _parse_frontmatter(current_content)
        prompt = settings.enrich.prompt.format(
            current_content=current_content,
            type=fm.get("type", ""),
            current_date=datetime.now().strftime("%Y-%m-%d"),
        )
        try:
            from services.llm import call_llm
            enriched = await call_llm(
                prompt,
                settings.enrich,
                settings.anthropic_api_key,
                settings.openrouter_api_key,
            )
            filepath.write_text(enriched, encoding="utf-8")
            _set_enrich_true(filepath)
            done += 1
        except Exception as exc:
            log.error("Enrich error [%s]: %s", filepath.name, exc)
            errors += 1

        processed = done + errors
        if processed % 5 == 0 or processed == total:
            try:
                await status_msg.edit_text(
                    f"✨ Обогащение: {processed}/{total}\n"
                    f"✅ Готово: {done}  ❌ Ошибок: {errors}"
                )
            except Exception:
                pass

    await status_msg.edit_text(
        f"✅ Обогащение завершено.\n"
        f"Обработано: {done}  ❌ Ошибок: {errors}"
    )


async def _run_reconciler(
    analysis: str,
    title: str,
    url: str,
    channel_name: str,
    summary_note_path: str,
    settings: Settings,
) -> None:
    """Запускает reconciler + generator если включён в настройках. Ошибки не прерывают основной поток."""
    if not settings.reconciler.enabled:
        return
    try:
        entities = extract_entities_yaml(analysis)
        if not entities:
            log.info("Reconciler [%s]: нет сущностей в YAML-блоке", title)
            return
        index = load_index(settings.reconciler.index_path)
        source_info = {
            "title": title,
            "url": url,
            "channel": channel_name,
            "date": datetime.now().strftime("%Y-%m-%d"),
        }
        plan = build_plan(entities, index, source_info)
        log.info("Reconciler plan [%s]: %s", title, plan["summary"])

        entries = await apply_plan_with_llm(
            plan=plan,
            index=index,
            vault_path=settings.reconciler.vault_path,
            summary_note_path=summary_note_path,
            settings=settings.llm,
            anthropic_api_key=settings.anthropic_api_key,
            openrouter_api_key=settings.openrouter_api_key,
        )
        for entry in entries:
            log.info("  %s", entry)

        save_index(index, settings.reconciler.index_path)
    except Exception as exc:
        log.error("Reconciler error [%s]: %s", title, exc)


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
        log.warning("Transcript error [%s]: %s", url, e)
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
    await _run_reconciler(analysis, result.title, url, result.channel_name, str(filepath), settings)
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

    await _run_reconciler(analysis, result.title, url, result.channel_name, str(filepath), settings)

    preview = html.escape(analysis[:3000])
    if len(analysis) > 3000:
        preview += "\n\n<i>...конспект обрезан для предпросмотра...</i>"

    await status_msg.delete()
    await message.answer(
        f"<b>{html.escape(result.title)}</b>\n\n"
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

    delay = settings.channel.batch_delay_seconds
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
            try:
                await status_msg.edit_text(
                    f"❌ Видео {idx}/{total}: ошибка\n"
                    f"<i>{label}</i>\n{error}\n\n"
                    f"✅ Готово: {len(saved)}  ❌ Пропущено: {len(failed)}"
                )
            except Exception:
                pass
        else:
            saved.append((title, filepath))

        if idx < total:
            await asyncio.sleep(delay)

    lines = [f"<b>Канал обработан.</b> Видео: {total}\n"]
    lines.append(f"✅ Сохранено: {len(saved)}   ❌ Пропущено: {len(failed)}")

    if failed:
        lines.append("\n<b>Ошибки:</b>")
        for label, reason in failed:
            lines.append(f"• <i>{label}</i>\n  {reason}")

    try:
        await status_msg.edit_text("\n".join(lines))
    except Exception:
        pass
