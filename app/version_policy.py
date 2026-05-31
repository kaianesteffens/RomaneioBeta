from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


_SEMVER_PATTERN = re.compile(
    r"^\s*[vV]?(?P<major>\d+)(?:\.(?P<minor>\d+))?(?:\.(?P<patch>\d+))?"
)


def parse_semantic_version(value: Any) -> tuple[int, int, int]:
    text = str(value or "").strip()
    match = _SEMVER_PATTERN.match(text)
    if not match:
        raise ValueError(f"Versao invalida: {value!r}")
    major = int(match.group("major"))
    minor = int(match.group("minor") or 0)
    patch = int(match.group("patch") or 0)
    return major, minor, patch


def compare_semantic_versions(left: Any, right: Any) -> int:
    left_parts = parse_semantic_version(left)
    right_parts = parse_semantic_version(right)
    if left_parts < right_parts:
        return -1
    if left_parts > right_parts:
        return 1
    return 0


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "sim", "yes", "on", "enabled", "habilitado"}:
            return True
        if normalized in {"0", "false", "nao", "no", "off", "disabled", "desabilitado"}:
            return False
    return bool(value)


@dataclass(frozen=True)
class MinimumVersionPolicy:
    current_version: str
    min_app_version: str | None
    force_update: bool
    is_outdated: bool
    should_block: bool
    should_warn: bool


def evaluate_minimum_version(config: Any, current_version: str) -> MinimumVersionPolicy:
    config_dict = config if isinstance(config, dict) else {}
    min_raw = config_dict.get("min_app_version")
    min_version = str(min_raw).strip() if min_raw is not None else ""
    force_update = _coerce_bool(config_dict.get("force_update", False))

    if not min_version:
        return MinimumVersionPolicy(
            current_version=str(current_version or "").strip(),
            min_app_version=None,
            force_update=force_update,
            is_outdated=False,
            should_block=False,
            should_warn=False,
        )

    try:
        is_outdated = compare_semantic_versions(current_version, min_version) < 0
    except ValueError:
        is_outdated = False

    return MinimumVersionPolicy(
        current_version=str(current_version or "").strip(),
        min_app_version=min_version,
        force_update=force_update,
        is_outdated=is_outdated,
        should_block=is_outdated and force_update,
        should_warn=is_outdated and not force_update,
    )
