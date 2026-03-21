1.1
# Создание новой заметки Obsidian

Создай заметку для базы знаний Obsidian на основе данных из видео.

Данные из видео:
- Название: {name}
- Тип (type): {entity_type}
- Домен: {domain}
- Поддомен: {sub_domain}
- ИИ-релевантность: {ai_relevance}
- Связан с: {relates_to}
- Что узнали из видео: {what_learned}

Источник:
- Видео: [{video_title}]({url})
- Канал: {channel}
- Дата: {date}

---

## Правила

- Верни ТОЛЬКО содержимое заметки, без пояснений и комментариев.
- Заполняй только то, что ЯВНО следует из данных выше. НЕ ПРИДУМЫВАЙ.
- Незаполненные поля оставляй пустыми (`""` или `[]`), не удаляй их.
- Секции тела, которые невозможно заполнить, оставляй как `TODO`.

---

## Frontmatter (обязателен для всех типов)

```yaml
---
name: "{name}"
type: {entity_type}
domain: [{domain}]
sub_domain: "{sub_domain}"
maturity: ""
cost: ""
ai_relevance: "{ai_relevance}"
suitable_for: []
project_stage: []
team_size: []
relates_to: [{relates_to_links}]
enrich: false
tags: []
created: "{date}"
updated: "{date}"
---
```

Допустимые значения:
- maturity: experimental | growing | established
- cost: free | freemium | paid | open-source
- ai_relevance: is_ai | integrates_with_ai | not_related
- suitable_for: mvp, saas, marketplace, api, bot, automation, content-site, internal-tool
- project_stage: idea, prototype, mvp, growth, scale
- team_size: solo, small, enterprise

---

## Тело заметки

### Общие секции (обязательны для всех типов)

```
## Суть
1-3 предложения. Что это и зачем существует.

## Заметки из источников
- [{video_title}]({url}) ({date}): {what_learned}
```

### Специфичные секции — выбери блок по значению type

**Если type = tool / framework / library / platform / service / cli / language:**
```
## Ключевые возможности
- ...

## Когда использовать
...

## Когда НЕ использовать
TODO

## Альтернативы
- [[Альтернатива]] — чем отличается

## Быстрый старт
TODO
```

**Если type = pattern / approach:**
```
## Как работает
...

## Когда применять
...

## Когда НЕ применять
TODO

## Примеры
- [[Case: ...]]
```

**Если type = concept:**
```
## Ключевая идея
...

## Как применять при оценке проекта
TODO

## Связанные концепции
- [[Concept: ...]]
```

**Если type = case:**
```
## Кто
...

## Что сделал
...

## Стек и подходы
- [[Tool: ...]]

## Ключевые инсайты
- ...

## Применимость к моим проектам
TODO
```
