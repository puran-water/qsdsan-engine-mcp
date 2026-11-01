"""Semantic stream-role helpers shared by session build and runtime lookup."""

from __future__ import annotations

from collections import defaultdict
from itertools import chain
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Sequence
from weakref import WeakKeyDictionary


_ROLE_ORDER = [
    "influent",
    "effluent",
    "was",
    "ras",
    "internal_recycle",
    "sludge_return",
    "sludge",
    "retentate",
    "permeate",
    "biogas",
    "methanol_feed",
    "external_carbon",
    "digestate",
]

_SYSTEM_ROLE_STORE: "WeakKeyDictionary[Any, Dict[str, List[str]]]" = WeakKeyDictionary()
_SYSTEM_ROLE_STORE_BY_ID: Dict[int, Dict[str, List[str]]] = {}
_SYSTEM_ROLE_INDEX_BY_ID: Dict[int, Dict[str, List[str]]] = {}


def _normalize_token(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _ordered_unique(values: Iterable[str]) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for value in values:
        normalized = _normalize_token(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def canonicalize_stream_roles(*candidates: str | None) -> List[str]:
    """
    Convert free-form stream role/name hints into canonical semantic roles.

    The seed layer still emits some generic labels such as ``feed`` or
    ``recycle``. This helper upgrades those into stable runtime roles that the
    build and SRT layers can consume without relying on stream names or outlet
    indices.
    """

    tokens = {
        token
        for candidate in candidates
        if candidate
        for token in _normalize_token(candidate).split("_")
        if token
    }
    joined = " ".join(_normalize_token(candidate or "") for candidate in candidates)

    roles: List[str] = []

    if {"methanol", "meoh"} & tokens or "methanol" in joined:
        roles.extend(["methanol_feed", "external_carbon"])
    elif {"external", "carbon"} <= tokens or "external_carbon" in joined or "carbon_feed" in joined:
        roles.extend(["external_carbon"])

    if "influent" in tokens or "feed" in tokens:
        if "methanol_feed" not in roles and "external_carbon" not in roles:
            roles.append("influent")

    if "effluent" in tokens or "permeate" in tokens:
        roles.append("effluent")
    if "permeate" in tokens:
        roles.append("permeate")

    if {"was", "waste"} & tokens or "wastage" in tokens or "waste_sludge" in joined:
        roles.extend(["was", "sludge"])

    if "ras" in tokens or "sludge_return" in joined or ("return" in tokens and "sludge" in tokens):
        roles.extend(["ras", "sludge_return"])

    if "internal_recycle" in joined or ("recycle" in tokens and "ras" not in tokens):
        roles.append("internal_recycle")

    if "sludge" in tokens and "sludge_return" not in roles and "was" not in roles:
        roles.append("sludge")

    if "retentate" in tokens:
        roles.append("retentate")

    if "biogas" in tokens or ("gas" in tokens and "biogas" in joined):
        roles.append("biogas")

    if "digestate" in tokens:
        roles.append("digestate")

    normalized_roles = _ordered_unique(roles)
    if normalized_roles:
        ordered = [role for role in _ROLE_ORDER if role in normalized_roles]
        ordered.extend(role for role in normalized_roles if role not in _ROLE_ORDER)
        return ordered

    fallback = _ordered_unique(candidate for candidate in candidates if candidate)
    return fallback


def add_stream_roles(
    role_map: MutableMapping[str, List[str]],
    stream_id: str,
    roles: Iterable[str],
) -> None:
    """Merge one or more roles onto a stream ID."""

    existing = list(role_map.get(stream_id, []))
    merged = _ordered_unique(chain(existing, roles))
    if merged:
        role_map[stream_id] = merged


def invert_stream_roles(role_map: Mapping[str, Sequence[str]]) -> Dict[str, List[str]]:
    """Return canonical role -> stream ID index."""

    inverted: Dict[str, List[str]] = defaultdict(list)
    for stream_id, roles in role_map.items():
        for role in _ordered_unique(roles):
            if stream_id not in inverted[role]:
                inverted[role].append(stream_id)
    return dict(inverted)


def derive_session_stream_roles(session: Any) -> Dict[str, List[str]]:
    """
    Return explicit role tags, or derive roles for manually authored sessions.
    """

    from qsdsan_engine_mcp.core.unit_registry import get_unit_spec

    role_map: Dict[str, List[str]] = {}
    explicit = getattr(session, "stream_roles", {}) or {}
    if explicit:
        for stream_id, roles in explicit.items():
            add_stream_roles(role_map, stream_id, roles)
        return role_map

    for stream_id, config in getattr(session, "streams", {}).items():
        default_roles = canonicalize_stream_roles(
            getattr(config, "stream_type", None),
        )
        add_stream_roles(role_map, stream_id, default_roles)

    for _, config in getattr(session, "units", {}).items():
        outputs = list(getattr(config, "outputs", None) or [])
        if not outputs:
            continue
        try:
            spec = get_unit_spec(config.unit_type)
            default_output_roles = list(getattr(spec, "output_roles", []) or [])
        except Exception:
            default_output_roles = []

        for index, stream_id in enumerate(outputs):
            if not stream_id:
                continue
            if index < len(default_output_roles):
                add_stream_roles(
                    role_map,
                    stream_id,
                    canonicalize_stream_roles(default_output_roles[index]),
                )

    return role_map


def _iter_system_streams(system: Any, extra_streams: Mapping[str, Any] | None = None):
    seen = set()
    for stream in chain(
        getattr(system, "streams", ()) or (),
        getattr(system, "feeds", ()) or (),
        getattr(system, "products", ()) or (),
        (extra_streams or {}).values(),
    ):
        if stream is None:
            continue
        marker = id(stream)
        if marker in seen:
            continue
        seen.add(marker)
        yield stream


def _cache_system_roles(system: Any, role_map: Dict[str, List[str]]) -> None:
    role_index = invert_stream_roles(role_map)
    try:
        _SYSTEM_ROLE_STORE[system] = role_map
    except TypeError:
        pass
    system_id = id(system)
    _SYSTEM_ROLE_STORE_BY_ID[system_id] = role_map
    _SYSTEM_ROLE_INDEX_BY_ID[system_id] = role_index


def _get_cached_role_map(system: Any) -> Dict[str, List[str]]:
    role_map = getattr(system, "_stream_roles", None)
    if role_map:
        return role_map

    try:
        role_map = _SYSTEM_ROLE_STORE.get(system)
    except TypeError:
        role_map = None
    if role_map:
        return role_map

    return _SYSTEM_ROLE_STORE_BY_ID.get(id(system), {})


def attach_stream_roles_to_system(
    system: Any,
    role_map: Mapping[str, Sequence[str]],
    extra_streams: Mapping[str, Any] | None = None,
) -> Dict[str, List[str]]:
    """Persist semantic role tags onto a compiled QSDsan ``System``."""

    normalized: Dict[str, List[str]] = {}
    for stream_id, roles in role_map.items():
        merged = _ordered_unique(roles)
        if merged:
            normalized[stream_id] = merged

    _cache_system_roles(system, normalized)
    try:
        system._stream_roles = normalized
        system._streams_by_role = invert_stream_roles(normalized)
    except AttributeError:
        pass

    streams_by_id = {
        getattr(stream, "ID", ""): stream
        for stream in _iter_system_streams(system, extra_streams)
        if getattr(stream, "ID", "")
    }

    for stream_id, roles in normalized.items():
        stream = streams_by_id.get(stream_id)
        if stream is None:
            continue
        try:
            stream._stream_roles = tuple(roles)
        except AttributeError:
            pass

    return normalized


def get_streams_by_role(
    system: Any,
    *roles: str,
    exclude_roles: Iterable[str] | None = None,
) -> List[Any]:
    """Resolve compiled system streams by semantic role tags."""

    role_map = _get_cached_role_map(system)
    if not role_map:
        return []

    wanted = {_normalize_token(role) for role in roles if role}
    excluded = {_normalize_token(role) for role in (exclude_roles or ()) if role}
    stream_ids = []
    for stream_id, stream_roles in role_map.items():
        role_set = {_normalize_token(role) for role in stream_roles}
        if wanted and not (role_set & wanted):
            continue
        if excluded and (role_set & excluded):
            continue
        stream_ids.append(stream_id)

    if not stream_ids:
        return []

    stream_lookup = {
        getattr(stream, "ID", ""): stream
        for stream in _iter_system_streams(system)
        if getattr(stream, "ID", "")
    }
    resolved = []
    seen = set()
    for stream_id in stream_ids:
        stream = stream_lookup.get(stream_id)
        if stream is None:
            continue
        marker = id(stream)
        if marker in seen:
            continue
        seen.add(marker)
        resolved.append(stream)
    return resolved


__all__ = [
    "add_stream_roles",
    "attach_stream_roles_to_system",
    "canonicalize_stream_roles",
    "derive_session_stream_roles",
    "get_streams_by_role",
    "invert_stream_roles",
]
