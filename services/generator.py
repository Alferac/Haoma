"""
Generation Engine — Этап 3 пайплайна.

Принимает план из reconciler и для каждого действия:
- create:   LLM генерирует новую заметку по шаблону + данным из видео
- update:   LLM читает текущую заметку и дописывает новую информацию
- skip:     собирает wikilinks упомянутых сущностей и добавляет их в заметку-выжимку
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import aiofiles

from config import LLMSettings
from reconciler import make_key
from services.llm import call_llm

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────
#  PROMPTS
# ──────────────────────────────────────────────

_CREATE_PROMPT = """\
Создай заметку об инструменте/концепции для базы знаний Obsidian.

Данные из видео:
- Название: {name}
- Тип: {entity_type}
- Домен: {domain}
- Поддомен: {sub_domain}
- ИИ-релевантность: {ai_relevance}
- Связан с: {relates_to}
- Что узнали из видео: {what_learned}

Источник:
- Видео: [{video_title}]({url})
- Канал: {channel}
- Дата: {date}

Создай заметку строго по шаблону ниже. Заполняй только то, что ЯВНО следует из данных выше.
Поля, которые неизвестны, — оставляй пустыми ("" или []).
НЕ ПРИДУМЫВАЙ информацию. Не добавляй ничего от себя.
Незаполненные секции тела заметки оставляй как "TODO".
Верни ТОЛЬКО содержимое заметки, без пояснений.

---
type: {entity_type}
domain: [{domain}]
sub_domain: "{sub_domain}"
maturity: ""
cost: ""
ai_relevance: "{ai_relevance}"
suitable_for: []
project_stage: []
team_size: []
tags: []
---

## Что это
{what_learned}

## Ключевые возможности
- TODO

## Когда использовать
TODO

## Альтернативы
{relates_to_links}

## Заметки из источников
- [{video_title}]({url}) ({date}): {what_learned}
"""

_UPDATE_PROMPT = """\
Обнови заметку в базе знаний Obsidian новой информацией из видео.

Текущее содержимое заметки:
{current_content}

Новая информация из видео:
- Источник: [{video_title}]({url})
- Канал: {channel}
- Дата: {date}
- Роль в видео: {role_in_video}
- Что узнали: {what_learned}

Правила обновления:
1. Добавь запись в секцию "## Заметки из источников":
   - [{video_title}]({url}) ({date}): {what_learned}
2. Если новая информация существенно дополняет поля frontmatter (maturity, cost, ai_relevance) — обнови их.
3. Если появились новые ключевые возможности — добавь в "## Ключевые возможности".
4. Всё остальное оставь без изменений.
5. Верни ПОЛНОЕ содержимое обновлённой заметки, без пояснений.
"""


# ──────────────────────────────────────────────
#  GENERATION HELPERS
# ──────────────────────────────────────────────

async def _generate_create(
    decision: dict,
    settings: LLMSettings,
    anthropic_api_key: str,
    openrouter_api_key: str,
) -> str:
    e = decision["entity_data"]
    src = decision["source"]
    domain_str = ", ".join(e.get("domain", []))
    relates_to_links = "  ".join(f"[[{r}]]" for r in e.get("relates_to", [])) or "TODO"

    prompt = _CREATE_PROMPT.format(
        name=e["name"],
        entity_type=e.get("entity_type", "tool"),
        domain=domain_str,
        sub_domain=e.get("sub_domain", ""),
        ai_relevance=e.get("ai_relevance", ""),
        relates_to=", ".join(e.get("relates_to", [])),
        relates_to_links=relates_to_links,
        what_learned=e.get("what_learned", ""),
        video_title=src.get("title", ""),
        url=src.get("url", ""),
        channel=src.get("channel", ""),
        date=src.get("date", ""),
    )
    return await call_llm(prompt, settings, anthropic_api_key, openrouter_api_key)


async def _generate_update(
    current_content: str,
    decision: dict,
    settings: LLMSettings,
    anthropic_api_key: str,
    openrouter_api_key: str,
) -> str:
    e = decision["entity_data"]
    src = decision["source"]

    prompt = _UPDATE_PROMPT.format(
        current_content=current_content,
        video_title=src.get("title", ""),
        url=src.get("url", ""),
        channel=src.get("channel", ""),
        date=src.get("date", ""),
        role_in_video=decision.get("role_in_video", ""),
        what_learned=e.get("what_learned", ""),
    )
    return await call_llm(prompt, settings, anthropic_api_key, openrouter_api_key)


# ──────────────────────────────────────────────
#  APPLY PLAN WITH LLM
# ──────────────────────────────────────────────

async def apply_plan_with_llm(
    plan: dict,
    index: dict,
    vault_path: str,
    summary_note_path: str,
    settings: LLMSettings,
    anthropic_api_key: str,
    openrouter_api_key: str,
) -> list[str]:
    """
    Применяет план с LLM-генерацией:
    - create  → LLM пишет новую заметку
    - update  → LLM дополняет существующую
    - skip    → wikilink упомянутой сущности добавляется в заметку-выжимку
    """
    entries: list[str] = []
    mentioned_links: list[str] = []

    for decision in plan["actions"]:
        action = decision["action"]
        name = decision["entity_name"]

        if action == "create":
            target = decision["target_file"]
            full_path = os.path.join(vault_path, target)
            try:
                content = await _generate_create(
                    decision, settings, anthropic_api_key, openrouter_api_key
                )
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                async with aiofiles.open(full_path, "w", encoding="utf-8") as f:
                    await f.write(content)

                e = decision["entity_data"]
                key = make_key(name)
                index["entities"][key] = {
                    "name": name,
                    "entity_type": e.get("entity_type", "tool"),
                    "domain": e.get("domain", []),
                    "sub_domain": e.get("sub_domain", ""),
                    "file_path": target,
                    "sources_count": 1,
                    "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    "aliases": [],
                }
                entries.append(f"CREATED: {target}")
                log.info("Generator CREATED: %s", target)
            except Exception as exc:
                entries.append(f"ERROR create {name}: {exc}")
                log.error("Generator create error [%s]: %s", name, exc)

        elif action == "update":
            existing_file = decision.get("existing_file", "")
            full_path = os.path.join(vault_path, existing_file)
            try:
                if os.path.exists(full_path):
                    async with aiofiles.open(full_path, "r", encoding="utf-8") as f:
                        current_content = await f.read()
                    updated = await _generate_update(
                        current_content, decision, settings, anthropic_api_key, openrouter_api_key
                    )
                    async with aiofiles.open(full_path, "w", encoding="utf-8") as f:
                        await f.write(updated)

                    key = decision["existing_key"]
                    if key in index["entities"]:
                        index["entities"][key]["sources_count"] += 1
                        index["entities"][key]["last_updated"] = (
                            datetime.now(timezone.utc).strftime("%Y-%m-%d")
                        )
                    entries.append(f"UPDATED: {existing_file}")
                    log.info("Generator UPDATED: %s", existing_file)
                else:
                    entries.append(f"WARNING: {existing_file} not found, skipped")
            except Exception as exc:
                entries.append(f"ERROR update {name}: {exc}")
                log.error("Generator update error [%s]: %s", name, exc)

        elif action == "skip":
            existing_key = decision.get("existing_key")
            if existing_key and existing_key in index["entities"]:
                entity_name = index["entities"][existing_key]["name"]
                mentioned_links.append(f"[[{entity_name}]]")
            entries.append(f"SKIP: {name} (role={decision['role_in_video']})")

    # Добавляем wikilinks упомянутых сущностей в заметку-выжимку
    if mentioned_links and summary_note_path and os.path.exists(summary_note_path):
        try:
            links_block = "\n\n## Упомянуто\n" + "  ".join(mentioned_links)
            async with aiofiles.open(summary_note_path, "a", encoding="utf-8") as f:
                await f.write(links_block)
            entries.append(f"LINKED {len(mentioned_links)} mentioned → summary")
        except Exception as exc:
            log.error("Generator mentions link error: %s", exc)

    return entries
