"""
Reconciliation Engine для справочной системы Obsidian.

Принимает извлечённые сущности из YouTube-выжимки,
сверяет с индексом существующих заметок,
выдаёт план действий: create / update / skip.

Подход A: файловый индекс (entity_index.json).
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from difflib import SequenceMatcher


# ──────────────────────────────────────────────
#  0. ENTITY EXTRACTION FROM MARKDOWN
# ──────────────────────────────────────────────

def extract_entities_yaml(markdown: str) -> list[dict]:
    """
    Извлекает список сущностей из YAML-блока в markdown.
    Ищет блок ```yaml ... ``` содержащий ключ 'entities'.
    Возвращает пустой список если блок не найден или невалиден.
    """
    import yaml

    pattern = re.compile(r"```(?:yaml)?\s*\n(.*?)\n```", re.DOTALL)
    for match in pattern.finditer(markdown):
        try:
            data = yaml.safe_load(match.group(1))
            if isinstance(data, dict) and "entities" in data:
                entities = data["entities"]
                if isinstance(entities, list):
                    return entities
        except Exception:
            continue
    return []


# ──────────────────────────────────────────────
#  1. INDEX OPERATIONS
# ──────────────────────────────────────────────

def load_index(index_path: str) -> dict:
    """Загружает индекс сущностей. Если файла нет — создаёт пустой."""
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "_meta": {
            "version": 1,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "total_entities": 0
        },
        "entities": {}
    }


def save_index(index: dict, index_path: str):
    """Сохраняет индекс с обновлённой метой. Создаёт директорию если нет."""
    index["_meta"]["last_updated"] = datetime.now(timezone.utc).isoformat()
    index["_meta"]["total_entities"] = len(index["entities"])
    os.makedirs(os.path.dirname(index_path), exist_ok=True)
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


# ──────────────────────────────────────────────
#  2. MATCHING: поиск сущности в индексе
# ──────────────────────────────────────────────

def normalize(name: str) -> str:
    """Нормализует имя для сравнения: lowercase, убирает спецсимволы."""
    return re.sub(r"[^a-zа-яё0-9]", "", name.lower())


def make_key(name: str) -> str:
    """Создаёт ключ для индекса из имени."""
    return re.sub(r"[^a-zа-яё0-9-]", "", name.lower().replace(" ", "-"))


def find_in_index(name: str, index: dict) -> str | None:
    """
    Ищет сущность в индексе по:
    1. Точному ключу
    2. Алиасам
    3. Нечёткому совпадению (>85%)

    Возвращает ключ найденной сущности или None.
    """
    entities = index["entities"]
    norm_name = normalize(name)
    key_candidate = make_key(name)

    # 1. Точный ключ
    if key_candidate in entities:
        return key_candidate

    # 2. Поиск по алиасам
    for key, entity in entities.items():
        aliases = [normalize(a) for a in entity.get("aliases", [])]
        if norm_name in aliases:
            return key

    # 3. Нечёткое совпадение по имени
    for key, entity in entities.items():
        ratio = SequenceMatcher(None, norm_name, normalize(entity["name"])).ratio()
        if ratio > 0.85:
            return key

    return None


# ──────────────────────────────────────────────
#  3. DECISION: что делать с каждой сущностью
# ──────────────────────────────────────────────

# Правила:
# role=primary/recommended → create если нет, update если есть
# role=compared            → update если есть, skip если нет
# role=mentioned           → skip всегда (только ссылка в выжимке)

ROLE_RULES = {
    "primary":     {"if_exists": "update", "if_new": "create"},
    "recommended": {"if_exists": "update", "if_new": "create"},
    "compared":    {"if_exists": "update", "if_new": "skip"},
    "mentioned":   {"if_exists": "skip",   "if_new": "skip"},
}


def decide_action(entity: dict, index: dict) -> dict:
    """
    Принимает одну извлечённую сущность,
    возвращает решение с контекстом.
    """
    name = entity["name"]
    role = entity.get("role_in_video", "mentioned")
    rules = ROLE_RULES.get(role, ROLE_RULES["mentioned"])

    existing_key = find_in_index(name, index)

    if existing_key:
        existing = index["entities"][existing_key]
        action = rules["if_exists"]
        return {
            "action": action,
            "entity_name": name,
            "existing_key": existing_key,
            "existing_file": existing.get("file_path"),
            "role_in_video": role,
            "what_learned": entity.get("what_learned", ""),
            "reason": f"Найдено в индексе как '{existing['name']}' → {action}"
        }
    else:
        action = rules["if_new"]
        return {
            "action": action,
            "entity_name": name,
            "existing_key": None,
            "existing_file": None,
            "role_in_video": role,
            "what_learned": entity.get("what_learned", ""),
            "reason": f"Не найдено в индексе, role={role} → {action}"
        }


# ──────────────────────────────────────────────
#  4. PLAN: обработка всех сущностей из видео
# ──────────────────────────────────────────────

# Маппинг entity_type → папка Obsidian
FOLDER_MAP = {
    "tool":      "Tools",
    "framework": "Tools",
    "library":   "Tools",
    "platform":  "Tools",
    "service":   "Tools",
    "cli":       "Tools",
    "language":  "Tools",
    "pattern":   "Architecture",
    "approach":  "Business",
    "concept":   "Concepts",
    "case":      "Cases",
}


def build_plan(extracted_entities: list[dict], index: dict, source_info: dict) -> dict:
    """
    Строит полный план действий для одного видео.

    Args:
        extracted_entities: список сущностей из YAML-блока выжимки
        index: текущий индекс
        source_info: {"title": "...", "url": "...", "channel": "...", "date": "..."}

    Returns:
        {
            "source": source_info,
            "actions": [...],
            "summary": {"create": N, "update": N, "skip": N}
        }
    """
    actions = []
    summary = {"create": 0, "update": 0, "skip": 0}

    for entity in extracted_entities:
        decision = decide_action(entity, index)

        # Добавляем метаданные для генерации
        decision["entity_data"] = entity
        decision["source"] = source_info

        if decision["action"] == "create":
            folder = FOLDER_MAP.get(entity.get("entity_type", "tool"), "Tools")
            filename = make_key(entity["name"]) + ".md"
            decision["target_file"] = f"{folder}/{filename}"

        actions.append(decision)
        summary[decision["action"]] += 1

    return {
        "source": source_info,
        "actions": actions,
        "summary": summary
    }


# ──────────────────────────────────────────────
#  5. EXECUTE: применение плана
# ──────────────────────────────────────────────

def generate_new_note(decision: dict) -> str:
    """Генерирует markdown для новой заметки по шаблону."""
    e = decision["entity_data"]
    src = decision["source"]

    domain_str = ", ".join(e.get("domain", []))
    relates = ", ".join([f"[[{r}]]" for r in e.get("relates_to", [])])

    frontmatter = f"""---
type: {e.get("entity_type", "tool")}
domain: [{domain_str}]
sub_domain: "{e.get("sub_domain", "")}"
maturity: ""
cost: ""
ai_relevance: "{e.get("ai_relevance", "")}"
suitable_for: []
project_stage: []
team_size: []
tags: []
---"""

    body = f"""{frontmatter}

## Что это
{e.get("what_learned", "TODO: добавить описание")}

## Ключевые возможности
- TODO

## Когда использовать
TODO

## Альтернативы
{relates if relates else "- TODO"}

## Заметки из источников
- [{src.get("title", "?")}]({src.get("url", "")}) ({src.get("date", "?")}): {e.get("what_learned", "")}
"""
    return body


def generate_update_block(decision: dict) -> str:
    """Генерирует блок для добавления в существующую заметку."""
    e = decision["entity_data"]
    src = decision["source"]
    return f"- [{src.get('title', '?')}]({src.get('url', '')}) ({src.get('date', '?')}): {e.get('what_learned', '')}"


def apply_plan(plan: dict, index: dict, vault_path: str, dry_run: bool = True) -> list[str]:
    """
    Применяет план: создаёт/обновляет файлы и индекс.

    dry_run=True → только показывает что будет сделано (по умолчанию).
    """
    log = []

    for decision in plan["actions"]:
        action = decision["action"]
        name = decision["entity_name"]

        if action == "skip":
            log.append(f"  SKIP: {name} (role={decision['role_in_video']})")
            continue

        if action == "create":
            target = decision["target_file"]
            full_path = os.path.join(vault_path, target)
            content = generate_new_note(decision)

            if dry_run:
                log.append(f"  CREATE: {target}")
                log.append(f"    what_learned: {decision.get('what_learned', '')[:80]}...")
            else:
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(content)
                log.append(f"  CREATED: {target}")

            # Обновляем индекс
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
                "aliases": []
            }

        elif action == "update":
            existing_file = decision.get("existing_file", "")
            full_path = os.path.join(vault_path, existing_file)
            update_block = generate_update_block(decision)

            if dry_run:
                log.append(f"  UPDATE: {existing_file}")
                log.append(f"    + {update_block[:80]}...")
            else:
                if os.path.exists(full_path):
                    with open(full_path, "a", encoding="utf-8") as f:
                        f.write("\n" + update_block)
                    log.append(f"  UPDATED: {existing_file}")
                else:
                    log.append(f"  WARNING: файл {existing_file} не найден, пропуск")

            # Обновляем индекс
            key = decision["existing_key"]
            if key in index["entities"]:
                index["entities"][key]["sources_count"] += 1
                index["entities"][key]["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    return log


# ──────────────────────────────────────────────
#  6. REBUILD INDEX (утилита пересборки)
# ──────────────────────────────────────────────

def rebuild_index_from_vault(vault_path: str) -> dict:
    """
    Пересобирает индекс, сканируя frontmatter всех .md файлов в vault.
    Используй если индекс рассинхронизировался.
    """
    import yaml

    index = {
        "_meta": {
            "version": 1,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "total_entities": 0
        },
        "entities": {}
    }

    vault = Path(vault_path)
    for md_file in vault.rglob("*.md"):
        # Пропускаем служебные файлы
        if md_file.name.startswith("_"):
            continue

        content = md_file.read_text(encoding="utf-8")

        # Извлекаем frontmatter
        match = re.match(r"^---\n(.+?)\n---", content, re.DOTALL)
        if not match:
            continue

        try:
            fm = yaml.safe_load(match.group(1))
        except Exception:
            continue

        if not fm or "type" not in fm:
            continue

        name = md_file.stem.replace("-", " ").title()
        key = make_key(name)
        rel_path = str(md_file.relative_to(vault))

        # Считаем источники по строкам в "Заметки из источников"
        sources_count = content.count("](http")

        index["entities"][key] = {
            "name": name,
            "entity_type": fm.get("type", ""),
            "domain": fm.get("domain", []),
            "sub_domain": fm.get("sub_domain", ""),
            "file_path": rel_path,
            "sources_count": sources_count,
            "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "aliases": []
        }

    index["_meta"]["total_entities"] = len(index["entities"])
    return index
