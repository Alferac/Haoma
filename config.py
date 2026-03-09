from __future__ import annotations

import os
from dataclasses import dataclass, field
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


@dataclass
class SubtitleSettings:
    languages: list[str]
    prefer_manual: bool


@dataclass
class ChannelSettings:
    max_videos: int


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
class Settings:
    telegram_bot_token: str
    anthropic_api_key: str
    openrouter_api_key: str
    llm: LLMSettings
    subtitles: SubtitleSettings
    channel: ChannelSettings
    proxy: ProxySettings
    output: OutputSettings


def load_settings(config_path: str = "config.yaml") -> Settings:
    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"Файл конфигурации не найден: {config_path}")

    with open(config_file, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not telegram_token:
        raise ValueError("TELEGRAM_BOT_TOKEN не задан в .env")

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
        if first_line.startswith("#"):
            prompt_version = first_line.lstrip("#").strip()
            prompt_text = rest.lstrip("\n")
        else:
            prompt_text = raw_prompt
    else:
        prompt_text = llm_cfg.get("prompt")

    if not prompt_text:
        prompt_text = "Summarize the following transcript:\n\n{transcript}"

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
        anthropic_api_key=anthropic_key,
        openrouter_api_key=openrouter_key,
        llm=LLMSettings(
            provider=provider,
            model=llm_cfg.get("model", "claude-sonnet-4-6"),
            max_tokens=int(llm_cfg.get("max_tokens", 4000)),
            temperature=float(llm_cfg.get("temperature", 0.0)),
            prompt=prompt_text,
            prompt_version=prompt_version,
        ),
        subtitles=SubtitleSettings(
            languages=raw.get("subtitles", {}).get("languages", ["ru", "en"]),
            prefer_manual=raw.get("subtitles", {}).get("prefer_manual", True),
        ),
        channel=ChannelSettings(
            max_videos=int(raw.get("channel", {}).get("max_videos", 10)),
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
    )
