# Haoma — YouTube Summary Bot для Obsidian

Telegram-бот, который принимает ссылку на YouTube-видео или канал, извлекает субтитры без скачивания видео, анализирует их через LLM и сохраняет структурированный конспект в папку Obsidian в формате Markdown.

## Возможности

- **Видео** — отправь ссылку, получи конспект
- **Канал** — бот обработает N последних видео (настраивается)
- **Субтитры** — ручные и автоматические, приоритет по языку (ru → en → любой)
- **LLM** — Claude (Anthropic) или любая модель через OpenRouter
- **Obsidian-совместимый** вывод: YAML frontmatter, теги, структурированный Markdown
- **Конфигурируемый промт** — меняй структуру конспекта под себя

---

## Развёртывание на VPS (Ubuntu 22.04 / 24.04)

### Шаг 1 — Подключение к серверу

```bash
ssh user@your-server-ip
```

---

### Шаг 2 — Установка Python 3.11+

Ubuntu 22.04 поставляется с Python 3.10. Для гарантированной совместимости установим 3.11:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3.11 python3.11-venv python3.11-pip git curl
```

Проверка:

```bash
python3.11 --version   # должно быть Python 3.11.x
```

---

### Шаг 4 — Клонирование репозитория

```bash
git clone https://github.com/Alferac/Haoma.git ~/haoma
cd ~/haoma
```

---

### Шаг 5 — Виртуальное окружение и зависимости

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

---

### Шаг 6 — Получение токенов

#### Telegram Bot Token

1. Открой [@BotFather](https://t.me/BotFather) в Telegram
2. Отправь `/newbot`
3. Придумай имя и username для бота
4. Скопируй токен вида `7123456789:AAF...`

#### Anthropic API Key (если используешь Claude)

1. Зайди на [console.anthropic.com](https://console.anthropic.com)
2. API Keys → Create Key
3. Скопируй ключ вида `sk-ant-api03-...`

#### OpenRouter API Key (если используешь OpenRouter)

1. Зайди на [openrouter.ai/keys](https://openrouter.ai/keys)
2. Create Key
3. Скопируй ключ вида `sk-or-v1-...`

---

### Шаг 7 — Файл `.env`

```bash
cp .env.example .env
nano .env
```

Заполни значения:

```env
TELEGRAM_BOT_TOKEN=7123456789:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Сохрани: `Ctrl+O`, `Enter`, `Ctrl+X`.

Ограничь права доступа к файлу:

```bash
chmod 600 .env
```

---

### Шаг 8 — Настройка `config.yaml`

```bash
nano config.yaml
```

Основные параметры для изменения:

```yaml
llm:
  provider: "claude"          # claude | openrouter
  model: "claude-sonnet-4-6"  # модель LLM

channel:
  max_videos: 10              # максимум видео из канала за один запрос

output:
  folder: "/home/botuser/obsidian-notes"  # папка для Markdown-файлов
```

Создай папку для заметок:

```bash
mkdir -p /home/botuser/obsidian-notes
```

---

### Шаг 9 — Тестовый запуск

```bash
source .venv/bin/activate
python bot.py
```

Отправь боту ссылку на YouTube-видео. Если всё работает — останови (`Ctrl+C`) и перейди к следующему шагу.

---

### Шаг 10 — Автозапуск через systemd

Выйди обратно в root (или пользователя с sudo):

```bash
exit  # выход из botuser
```

Создай systemd-сервис:

```bash
sudo nano /etc/systemd/system/haoma-bot.service
```

Вставь содержимое (замени пути если нужно):

```ini
[Unit]
Description=Haoma YouTube Summary Telegram Bot
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=botuser
WorkingDirectory=/home/botuser/haoma
ExecStart=/home/botuser/haoma/.venv/bin/python bot.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

# Защита файловой системы
ProtectSystem=strict
ReadWritePaths=/home/botuser/haoma /home/botuser/obsidian-notes
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Активируй и запусти:

```bash
sudo systemctl daemon-reload
sudo systemctl enable haoma-bot
sudo systemctl start haoma-bot
```

Проверь статус:

```bash
sudo systemctl status haoma-bot
```

Вывод должен показывать `Active: active (running)`.

---

### Шаг 11 — Просмотр логов

```bash
# Последние 50 строк логов
sudo journalctl -u haoma-bot -n 50

# Логи в реальном времени
sudo journalctl -u haoma-bot -f
```

---

## Синхронизация заметок с Obsidian

Бот сохраняет `.md`-файлы в папку на сервере. Чтобы они появлялись в Obsidian на компьютере или телефоне, настрой синхронизацию.

### Вариант A — Syncthing (бесплатно, рекомендуется)

Устанавливается на VPS и на устройствах с Obsidian, синхронизирует папку в реальном времени.

**На VPS:**

```bash
sudo apt install -y syncthing
sudo systemctl enable --now syncthing@botuser
```

Открой веб-интерфейс: `http://your-server-ip:8384`
Добавь папку `/home/botuser/obsidian-notes` и подключи устройства.

**На компьютере:**
Скачай [Syncthing](https://syncthing.net/downloads/) → укажи ту же папку → добавь сервер как устройство.

**На Android/iOS:**
Приложение [Möbius Sync](https://www.mobiussync.com/) (iOS) или [Syncthing](https://play.google.com/store/apps/details?id=com.nutomic.syncthingandroid) (Android).



## Обновление бота

```bash
sudo su - botuser
cd ~/haoma
git pull origin main
source .venv/bin/activate
pip install -r requirements.txt  # если изменились зависимости
exit

sudo systemctl restart haoma-bot
sudo systemctl status haoma-bot
```

---

## Изменение промта или настроек

Редактируй `config.yaml` прямо на сервере — перезапуск не нужен для следующего сообщения, но рекомендуется:

```bash
sudo su - botuser
nano ~/haoma/config.yaml
exit
sudo systemctl restart haoma-bot
```

---

## Структура проекта

```
haoma/
├── bot.py               # Точка входа
├── config.py            # Загрузка настроек
├── config.yaml          # Настройки: промт, модель, папка, языки
├── .env                 # Секреты (не в git)
├── .env.example         # Шаблон
├── requirements.txt
├── handlers/
│   └── message.py       # Telegram-хендлер
└── services/
    ├── youtube.py       # Извлечение субтитров (youtube-transcript-api)
    ├── llm.py           # Интеграция LLM (Claude / OpenRouter)
    └── file_writer.py   # Сохранение Markdown
```

---

## Зависимости

| Пакет | Назначение |
|---|---|
| `aiogram` | Telegram Bot API (async) |
| `youtube-transcript-api` | Субтитры без скачивания видео |
| `scrapetube` | Список видео канала без API-ключа |
| `anthropic` | Claude API |
| `openai` | OpenRouter (совместимый интерфейс) |
| `httpx` | Получение заголовка видео |
| `aiofiles` | Асинхронная запись файлов |

---

## Лицензия

MIT
