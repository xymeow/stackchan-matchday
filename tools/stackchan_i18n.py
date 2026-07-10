#!/usr/bin/env python3
"""Small localization helpers shared by the watcher, replay, and setup tools."""

from __future__ import annotations

from typing import Any


SUPPORTED_LANGUAGES = ("zh", "en")
LANGUAGE_ALIASES = {
    "zh": "zh",
    "zh-cn": "zh",
    "zh-hans": "zh",
    "cn": "zh",
    "en": "en",
    "en-us": "en",
    "en-gb": "en",
}


class LocalizationError(ValueError):
    pass


def normalize_language(value: Any, *, path: str = "language") -> str:
    normalized = str(value or "zh").strip().lower().replace("_", "-")
    language = LANGUAGE_ALIASES.get(normalized)
    if language is None:
        supported = ", ".join(SUPPORTED_LANGUAGES)
        raise LocalizationError(f"{path} must be one of: {supported}")
    return language


def resolve_text(
    value: Any,
    language: str,
    *,
    path: str,
    fallback: str = "",
) -> str:
    """Resolve a legacy string or a {zh,en,default} localized leaf."""
    if value is None:
        return fallback
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, dict):
        raise LocalizationError(f"{path} must be a string or localized object")

    unknown = set(value) - {"zh", "en", "zh-CN", "en-US", "default"}
    if unknown:
        raise LocalizationError(
            f"{path} has unsupported language keys: {', '.join(sorted(map(str, unknown)))}"
        )
    candidates = (
        value.get(language),
        value.get("zh-CN" if language == "zh" else "en-US"),
        value.get("default"),
    )
    for candidate in candidates:
        if candidate is None:
            continue
        if not isinstance(candidate, str):
            raise LocalizationError(f"{path}.{language} must be a string")
        return candidate.strip()
    return fallback


def resolve_text_map(
    value: Any,
    language: str,
    *,
    path: str,
) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise LocalizationError(f"{path} must be an object")
    resolved: dict[str, str] = {}
    for key, leaf in value.items():
        name = resolve_text(leaf, language, path=f"{path}[{key!r}]")
        if name:
            resolved[str(key)] = name
    return resolved


def localized_pair(zh: str, en: str) -> dict[str, str]:
    return {"zh": zh, "en": en}


def pick(language: str, zh: str, en: str) -> str:
    return en if language == "en" else zh


def join_sentences(language: str, *parts: str) -> str:
    values = [part.strip() for part in parts if part and part.strip()]
    if language == "en":
        return " ".join(
            value if value.endswith((".", "!", "?")) else f"{value}."
            for value in values
        )
    return "".join(
        value if value.endswith(("。", "！", "？", "!", "?")) else f"{value}。"
        for value in values
    )
