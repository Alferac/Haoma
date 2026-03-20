from __future__ import annotations

import anthropic
from openai import AsyncOpenAI

from config import LLMSettings


async def call_llm(
    prompt: str,
    settings: LLMSettings,
    anthropic_api_key: str,
    openrouter_api_key: str,
) -> str:
    """Raw LLM call with a ready-made prompt. Used by generator and other services."""
    if settings.provider == "claude":
        return await _call_claude(prompt, settings, anthropic_api_key)
    elif settings.provider == "openrouter":
        return await _call_openrouter(prompt, settings, openrouter_api_key)
    else:
        raise RuntimeError(f"Неизвестный провайдер: {settings.provider}")


async def analyze_transcript(
    transcript: str,
    title: str,
    url: str,
    settings: LLMSettings,
    anthropic_api_key: str,
    openrouter_api_key: str,
    channel_name: str = "",
) -> str:
    """
    Send transcript to LLM and return Markdown analysis.

    Raises:
        RuntimeError: On API errors.
    """
    prompt = settings.prompt.format(
        title=title,
        url=url,
        transcript=transcript,
        channel_name=channel_name,
    )

    if settings.provider == "claude":
        return await _call_claude(prompt, settings, anthropic_api_key)
    elif settings.provider == "openrouter":
        return await _call_openrouter(prompt, settings, openrouter_api_key)
    else:
        raise RuntimeError(f"Неизвестный провайдер: {settings.provider}")


async def _call_claude(prompt: str, settings: LLMSettings, api_key: str) -> str:
    client = anthropic.AsyncAnthropic(api_key=api_key)
    try:
        message = await client.messages.create(
            model=settings.model,
            max_tokens=settings.max_tokens,
            temperature=settings.temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text
    except anthropic.APIStatusError as e:
        raise RuntimeError(f"Ошибка Claude API ({e.status_code}): {e.message}")
    except Exception as e:
        raise RuntimeError(f"Ошибка при обращении к Claude: {e}")


async def _call_openrouter(prompt: str, settings: LLMSettings, api_key: str) -> str:
    client = AsyncOpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
    )
    try:
        response = await client.chat.completions.create(
            model=settings.model,
            max_tokens=settings.max_tokens,
            temperature=settings.temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content
    except Exception as e:
        raise RuntimeError(f"Ошибка при обращении к OpenRouter: {e}")
