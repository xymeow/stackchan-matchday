#!/usr/bin/env python3
"""Deterministic global player names and nicknames for ESPN match events.

The catalog is deliberately independent from a single match configuration.  ESPN
athlete IDs are the stable primary key; normalized aliases are only a conservative
fallback for feeds or archived samples that omit the ID.  A duplicate alias is
considered ambiguous and never resolves to an arbitrary player.

Existing ``espn.player_names`` and ``espn.star_chants`` mappings remain supported
as per-match overlays through :func:`resolve_player_profile`.  Overlay values win
over catalog values, while an unknown player is returned with empty text so the
watcher can keep its existing raw-name fallback.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any


SCHEMA_VERSION = 1
SUPPORTED_LANGUAGES = ("zh", "en")
DEFAULT_CATALOG_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "espn_player_catalog.json"
)

_CATALOG_KEY_RE = re.compile(r"^espn:([0-9]+)$")
_SPACE_RE = re.compile(r"\s+")
_DOT_OR_COMMA_RE = re.compile(r"[.,，。]+")
_APOSTROPHE_SPACE_RE = re.compile(r"\s*'\s*")
_HYPHEN_SPACE_RE = re.compile(r"\s*-\s*")
_PUNCTUATION_TRANSLATION = str.maketrans(
    {
        "\u2018": "'",  # left single quotation mark
        "\u2019": "'",  # right single quotation mark
        "\u201b": "'",  # single high-reversed-9 quotation mark
        "\u02bc": "'",  # modifier letter apostrophe
        "\uff07": "'",  # full-width apostrophe
        "`": "'",
        "\u2010": "-",  # hyphen
        "\u2011": "-",  # non-breaking hyphen
        "\u2012": "-",  # figure dash
        "\u2013": "-",  # en dash
        "\u2014": "-",  # em dash
        "\u2212": "-",  # minus sign
        "\ufe63": "-",  # small hyphen-minus
        "\uff0d": "-",  # full-width hyphen-minus
    }
)
_LATIN_FOLD_TRANSLATION = str.maketrans(
    {
        "æ": "ae",
        "ð": "d",
        "đ": "d",
        "ı": "i",
        "ł": "l",
        "ø": "o",
        "œ": "oe",
        "þ": "th",
        "ß": "ss",
    }
)


class PlayerCatalogError(ValueError):
    """Raised when a player catalog does not satisfy the schema."""


def normalize_player_alias(value: Any) -> str:
    """Return a stable lookup form for a player name or ESPN ID.

    Matching is case-insensitive and tolerates Unicode compatibility forms,
    diacritics, curly apostrophes, typographic hyphens, and spacing around those
    characters.  Periods and commas are treated as spaces so ESPN short names
    such as ``L. Yamal`` also match ``L Yamal``.
    """

    text = unicodedata.normalize("NFKC", str(value or "")).translate(
        _PUNCTUATION_TRANSLATION
    )
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(
        character for character in text if not unicodedata.combining(character)
    )
    text = text.translate(_LATIN_FOLD_TRANSLATION)
    text = _DOT_OR_COMMA_RE.sub(" ", text)
    text = _APOSTROPHE_SPACE_RE.sub("'", text)
    text = _HYPHEN_SPACE_RE.sub("-", text)
    return _SPACE_RE.sub(" ", text).strip()


def _language(value: str) -> str:
    normalized = str(value or "zh").strip().lower().replace("_", "-")
    if normalized.startswith("zh"):
        return "zh"
    if normalized.startswith("en"):
        return "en"
    raise PlayerCatalogError(
        f"language must be one of: {', '.join(SUPPORTED_LANGUAGES)}"
    )


def _localized_field(
    value: Any,
    *,
    path: str,
    require_both: bool,
) -> Mapping[str, str]:
    if not isinstance(value, dict):
        raise PlayerCatalogError(f"{path} must be an object with zh/en text")
    unknown = set(value) - set(SUPPORTED_LANGUAGES)
    if unknown:
        raise PlayerCatalogError(
            f"{path} has unsupported language keys: "
            + ", ".join(sorted(map(str, unknown)))
        )
    if require_both:
        missing = set(SUPPORTED_LANGUAGES) - set(value)
        if missing:
            raise PlayerCatalogError(
                f"{path} is missing: {', '.join(sorted(missing))}"
            )
    resolved: dict[str, str] = {}
    for language, raw_text in value.items():
        if not isinstance(raw_text, str) or not raw_text.strip():
            raise PlayerCatalogError(f"{path}.{language} must be a non-empty string")
        resolved[language] = raw_text.strip()
    if not resolved:
        raise PlayerCatalogError(f"{path} must contain at least one localized value")
    return MappingProxyType(resolved)


def _optional_localized_field(value: Any, *, path: str) -> Mapping[str, str]:
    if value is None:
        return MappingProxyType({})
    return _localized_field(value, path=path, require_both=False)


@dataclass(frozen=True)
class PlayerCatalogEntry:
    """One global player record keyed by ``espn:<athlete_id>``."""

    key: str
    athlete_id: str
    aliases: tuple[str, ...]
    display_names: Mapping[str, str]
    casual_names: Mapping[str, str]
    featured: bool
    goal_chants: Mapping[str, str]

    def display_name(self, language: str = "zh") -> str:
        return self.display_names[_language(language)]

    def casual_name(self, language: str = "zh") -> str:
        selected = _language(language)
        return self.casual_names.get(selected, "") or self.display_name(selected)

    def goal_chant(self, language: str = "zh") -> str:
        return self.goal_chants.get(_language(language), "")


@dataclass(frozen=True)
class ResolvedPlayerProfile:
    """Catalog values after applying the legacy per-match configuration overlay."""

    catalog_key: str | None
    display_name: str
    casual_name: str
    featured: bool
    goal_chant: str
    source: str


class PlayerCatalog:
    """Validated player records with ambiguity-safe ID and alias lookup."""

    def __init__(self, entries: Mapping[str, PlayerCatalogEntry]):
        self._entries = MappingProxyType(dict(entries))
        alias_index: defaultdict[str, set[str]] = defaultdict(set)
        for key, entry in self._entries.items():
            match_values = (
                *entry.aliases,
                *entry.display_names.values(),
            )
            for value in match_values:
                normalized = normalize_player_alias(value)
                if normalized:
                    alias_index[normalized].add(key)
        self._alias_index = MappingProxyType(
            {alias: frozenset(keys) for alias, keys in alias_index.items()}
        )

    @property
    def entries(self) -> Mapping[str, PlayerCatalogEntry]:
        return self._entries

    def get(self, key: str) -> PlayerCatalogEntry | None:
        normalized_key = _catalog_key(key)
        return self._entries.get(normalized_key) if normalized_key else None

    def resolve_alias(self, alias: str) -> PlayerCatalogEntry | None:
        keys = self._alias_index.get(normalize_player_alias(alias), frozenset())
        if len(keys) != 1:
            return None
        return self._entries[next(iter(keys))]

    def resolve(
        self,
        *,
        athlete_id: str = "",
        name: str = "",
        short_name: str = "",
    ) -> PlayerCatalogEntry | None:
        """Resolve by stable ESPN ID, otherwise by one unambiguous alias.

        A known ID is authoritative.  If it is absent or unknown, every supplied
        name must point to the same single record; conflicting or duplicate aliases
        return ``None`` rather than guessing.
        """

        raw_athlete_id = str(athlete_id or "").strip()
        if raw_athlete_id:
            key = _catalog_key(raw_athlete_id)
            return self._entries.get(key) if key else None

        candidate_groups: list[frozenset[str]] = []
        for value in (name, short_name):
            normalized = normalize_player_alias(value)
            if not normalized:
                continue
            keys = self._alias_index.get(normalized, frozenset())
            if keys:
                candidate_groups.append(keys)
        if not candidate_groups:
            return None
        candidates = set(candidate_groups[0])
        for group in candidate_groups[1:]:
            candidates.intersection_update(group)
        if len(candidates) != 1:
            return None
        return self._entries[next(iter(candidates))]

    @classmethod
    def from_dict(cls, payload: Any) -> "PlayerCatalog":
        if not isinstance(payload, dict):
            raise PlayerCatalogError("player catalog root must be an object")
        unknown = set(payload) - {"schema_version", "players"}
        if unknown:
            raise PlayerCatalogError(
                "player catalog has unsupported keys: "
                + ", ".join(sorted(map(str, unknown)))
            )
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise PlayerCatalogError(f"schema_version must be {SCHEMA_VERSION}")
        raw_players = payload.get("players")
        if not isinstance(raw_players, dict):
            raise PlayerCatalogError("players must be an object")

        entries: dict[str, PlayerCatalogEntry] = {}
        for raw_key, raw_entry in raw_players.items():
            key = str(raw_key)
            match = _CATALOG_KEY_RE.fullmatch(key)
            if match is None:
                raise PlayerCatalogError(
                    f"players[{key!r}] must use an espn:<numeric athlete_id> key"
                )
            if not isinstance(raw_entry, dict):
                raise PlayerCatalogError(f"players[{key!r}] must be an object")
            path = f"players[{key!r}]"
            allowed = {
                "aliases",
                "display_name",
                "casual_name",
                "featured",
                "goal_chant",
            }
            entry_unknown = set(raw_entry) - allowed
            if entry_unknown:
                raise PlayerCatalogError(
                    f"{path} has unsupported keys: "
                    + ", ".join(sorted(map(str, entry_unknown)))
                )

            aliases_raw = raw_entry.get("aliases", [])
            if not isinstance(aliases_raw, list) or any(
                not isinstance(alias, str) or not alias.strip()
                for alias in aliases_raw
            ):
                raise PlayerCatalogError(f"{path}.aliases must be a list of strings")
            aliases = tuple(dict.fromkeys(alias.strip() for alias in aliases_raw))
            display_names = _localized_field(
                raw_entry.get("display_name"),
                path=f"{path}.display_name",
                require_both=True,
            )
            casual_names = _optional_localized_field(
                raw_entry.get("casual_name"), path=f"{path}.casual_name"
            )
            goal_chants = _optional_localized_field(
                raw_entry.get("goal_chant"), path=f"{path}.goal_chant"
            )
            featured = raw_entry.get("featured", False)
            if not isinstance(featured, bool):
                raise PlayerCatalogError(f"{path}.featured must be a boolean")
            entries[key] = PlayerCatalogEntry(
                key=key,
                athlete_id=match.group(1),
                aliases=aliases,
                display_names=display_names,
                casual_names=casual_names,
                featured=featured,
                goal_chants=goal_chants,
            )
        return cls(entries)

    @classmethod
    def load(cls, path: str | Path) -> "PlayerCatalog":
        source = Path(path)
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except OSError as exc:
            raise PlayerCatalogError(f"cannot read player catalog {source}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise PlayerCatalogError(
                f"invalid JSON in player catalog {source}: {exc}"
            ) from exc
        return cls.from_dict(payload)


def _catalog_key(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    key = raw.casefold() if raw.casefold().startswith("espn:") else f"espn:{raw}"
    return key if _CATALOG_KEY_RE.fullmatch(key) else ""


def _overlay_text(value: Any, language: str, *, path: str) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, Mapping):
        raise PlayerCatalogError(f"{path} must be a string or zh/en object")
    selected = _language(language)
    raw = value.get(selected)
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise PlayerCatalogError(f"{path}.{selected} must be a string")
    return raw.strip()


def legacy_overlay_value(
    mapping: Mapping[str, Any] | None,
    *,
    athlete_id: str = "",
    name: str = "",
    short_name: str = "",
    language: str = "zh",
) -> str:
    """Read one normalized legacy mapping value without ambiguous first-match wins.

    An ID-keyed override (``espn:123`` or ``123``) wins over name keys.  If
    multiple raw keys normalize to the same candidate but contain conflicting
    values, the override is ignored.
    """

    if not mapping:
        return ""
    normalized: defaultdict[str, set[str]] = defaultdict(set)
    for raw_key, raw_value in mapping.items():
        key = normalize_player_alias(raw_key)
        value = _overlay_text(
            raw_value, language, path=f"legacy overlay[{raw_key!r}]"
        )
        if key and value:
            normalized[key].add(value)

    raw_id = str(athlete_id or "").strip()
    if raw_id:
        if raw_id.casefold().startswith("espn:"):
            id_keys = (raw_id, raw_id.split(":", 1)[1])
        else:
            id_keys = (f"espn:{raw_id}", raw_id)
        for candidate in id_keys:
            values = normalized.get(normalize_player_alias(candidate), set())
            if len(values) == 1:
                return next(iter(values))
            if len(values) > 1:
                return ""

    groups: list[set[str]] = []
    for candidate in (name, short_name):
        values = normalized.get(normalize_player_alias(candidate), set())
        if values:
            groups.append(values)
    if not groups:
        return ""
    values = set(groups[0])
    for group in groups[1:]:
        values.intersection_update(group)
    return next(iter(values)) if len(values) == 1 else ""


def resolve_player_profile(
    catalog: PlayerCatalog,
    *,
    athlete_id: str = "",
    name: str = "",
    short_name: str = "",
    language: str = "zh",
    player_names: Mapping[str, Any] | None = None,
    star_chants: Mapping[str, Any] | None = None,
) -> ResolvedPlayerProfile:
    """Resolve catalog text and apply legacy ``player_names``/``star_chants``.

    The returned empty ``display_name`` for ``source == 'unknown'`` is
    intentional: callers own the policy for raw names, jersey prefixes, and other
    fallbacks.
    """

    selected_language = _language(language)
    entry = catalog.resolve(
        athlete_id=athlete_id,
        name=name,
        short_name=short_name,
    )
    legacy_name = legacy_overlay_value(
        player_names,
        athlete_id=athlete_id,
        name=name,
        short_name=short_name,
        language=selected_language,
    )
    legacy_chant = legacy_overlay_value(
        star_chants,
        athlete_id=athlete_id,
        name=name,
        short_name=short_name,
        language=selected_language,
    )

    display_name = legacy_name or (
        entry.display_name(selected_language) if entry is not None else ""
    )
    catalog_casual_name = (
        entry.casual_names.get(selected_language, "") if entry is not None else ""
    )
    casual_name = catalog_casual_name or display_name
    goal_chant = legacy_chant or (
        entry.goal_chant(selected_language) if entry is not None else ""
    )
    has_legacy = bool(legacy_name or legacy_chant)
    if entry is not None and has_legacy:
        source = "catalog+legacy"
    elif entry is not None:
        source = "catalog"
    elif has_legacy:
        source = "legacy"
    else:
        source = "unknown"
    return ResolvedPlayerProfile(
        catalog_key=entry.key if entry is not None else None,
        display_name=display_name,
        casual_name=casual_name,
        featured=(entry.featured if entry is not None else False) or bool(goal_chant),
        goal_chant=goal_chant,
        source=source,
    )


def load_default_player_catalog() -> PlayerCatalog:
    return PlayerCatalog.load(DEFAULT_CATALOG_PATH)
