from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv

load_dotenv()


@dataclass
class LLMSettings:
    provider: Literal["claude", "openrouter"]
    model: str
    max_tokens: int
    temperature: float
    prompt: str
    prompt_version: str = ""
    create_prompt: str = ""
    create_prompt_version: str = ""
    update_prompt: str = ""
    update_prompt_version: str = ""


@dataclass
class SubtitleSettings:
    languages: list[str]
    prefer_manual: bool


@dataclass
class ChannelSettings:
    max_videos: int
    batch_delay_seconds: int


@dataclass
class ProxySettings:
    http: str
    https: str

    def as_dict(self) -> dict[str, str]:
        result = {}
        if self.http:
            result["http"] = self.http
        if self.https:
            result["https"] = self.https
        return result

    @property
    def enabled(self) -> bool:
        return bool(self.http or self.https)


@dataclass
class OutputSettings:
    folder: Path
    add_frontmatter: bool
    max_filename_length: int


@dataclass
class ReconcilerSettings:
    enabled: bool
    vault_path: str
    index_path: str


@dataclass
class Settings:
    telegram_bot_token: str
    telegram_proxy: str
    anthropic_api_key: str
    openrouter_api_key: str
    llm: LLMSettings
    subtitles: SubtitleSettings
    channel: ChannelSettings
    proxy: ProxySettings
    output: OutputSettings
    reconciler: ReconcilerSettings


def load_settings(config_path: str = "config.yaml") -> Settings:
    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"Файл конфигурации не найден: {config_path}")

    with open(config_file, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not telegram_token:
        raise ValueError("TELEGRAM_BOT_TOKEN не задан в .env")
    telegram_proxy = raw.get("telegram_proxy", "") or ""

    llm_cfg = raw.get("llm", {})
    provider = llm_cfg.get("provider", "claude")
    if provider not in ("claude", "openrouter"):
        raise ValueError(f"llm.provider должен быть 'claude' или 'openrouter', получено: {provider!r}")

    prompt_text: str | None = None
    prompt_version: str = ""
    prompt_file_rel = llm_cfg.get("prompt_file")
    if prompt_file_rel:
        prompt_file_path = config_file.parent.joinpath(prompt_file_rel)
        if not prompt_file_path.exists():
            raise FileNotFoundError(f"Файл промта не найден: {prompt_file_path}")
        raw_prompt = prompt_file_path.read_text(encoding="utf-8")
        first_line, _, rest = raw_prompt.partition("\n")
        prompt_version = first_line.strip()
        prompt_text = rest.lstrip("\n")
    else:
        prompt_text = llm_cfg.get("prompt")

    if not prompt_text:
        prompt_text = "Summarize the following transcript:\n\n{transcript}"

    def _load_prompt_file(rel_path: str) -> tuple[str, str]:
        """Returns (version, prompt_text) from a prompt file."""
        path = config_file.parent.joinpath(rel_path)
        if not path.exists():
            raise FileNotFoundError(f"Файл промта не найден: {path}")
        raw = path.read_text(encoding="utf-8")
        ver, _, body = raw.partition("\n")
        return ver.strip(), body.lstrip("\n")

    create_prompt_text = ""
    create_prompt_version = ""
    create_prompt_file_rel = llm_cfg.get("create_prompt_file")
    if create_prompt_file_rel:
        create_prompt_version, create_prompt_text = _load_prompt_file(create_prompt_file_rel)

    update_prompt_text = ""
    update_prompt_version = ""
    update_prompt_file_rel = llm_cfg.get("update_prompt_file")
    if update_prompt_file_rel:
        update_prompt_version, update_prompt_text = _load_prompt_file(update_prompt_file_rel)

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")

    if provider == "claude" and not anthropic_key:
        raise ValueError("ANTHROPIC_API_KEY не задан в .env (требуется для provider=claude)")
    if provider == "openrouter" and not openrouter_key:
        raise ValueError("OPENROUTER_API_KEY не задан в .env (требуется для provider=openrouter)")

    output_cfg = raw.get("output", {})
    output_folder = Path(output_cfg.get("folder", "output"))
    output_folder.mkdir(parents=True, exist_ok=True)

    return Settings(
        telegram_bot_token=telegram_token,
        telegram_proxy=telegram_proxy,
        anthropic_api_key=anthropic_key,
        openrouter_api_key=openrouter_key,
        llm=LLMSettings(
            provider=provider,
            model=llm_cfg.get("model", "claude-sonnet-4-6"),
            max_tokens=int(llm_cfg.get("max_tokens", 4000)),
            temperature=float(llm_cfg.get("temperature", 0.0)),
            prompt=prompt_text,
            prompt_version=prompt_version,
            create_prompt=create_prompt_text,
            create_prompt_version=create_prompt_version,
            update_prompt=update_prompt_text,
            update_prompt_version=update_prompt_version,
        ),
        subtitles=SubtitleSettings(
            languages=raw.get("subtitles", {}).get("languages", ["ru", "en"]),
            prefer_manual=raw.get("subtitles", {}).get("prefer_manual", True),
        ),
        channel=ChannelSettings(
            max_videos=int(raw.get("channel", {}).get("max_videos", 10)),
            batch_delay_seconds=int(raw.get("channel", {}).get("batch_delay_seconds", 60)),
        ),
        proxy=ProxySettings(
            http=raw.get("proxy", {}).get("http", "") or "",
            https=raw.get("proxy", {}).get("https", "") or "",
        ),
        output=OutputSettings(
            folder=output_folder,
            add_frontmatter=output_cfg.get("add_frontmatter", True),
            max_filename_length=int(output_cfg.get("max_filename_length", 100)),
        ),
        reconciler=ReconcilerSettings(
            enabled=raw.get("reconciler", {}).get("enabled", False),
            vault_path=raw.get("reconciler", {}).get("vault_path", ""),
            index_path=raw.get("reconciler", {}).get("index_path", ""),
        ),
    )
