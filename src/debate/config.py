#!/usr/bin/env python3
"""Configuration loading for the debate method."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import yaml

DEFAULT_DEBATE_CONFIG_PATH = Path("debate.yaml")
DEFAULT_DEBATE_STATE_PATH = Path(".debate/state.yaml")
OFF_MODEL_NAME = "off"
BUILT_IN_MODELS = ("deepseek-pro", "codex", OFF_MODEL_NAME)


@dataclass(frozen=True)
class DebateRuntimeConfig:
    """Resolved runtime settings for one debate session."""

    models: tuple[str, ...] = BUILT_IN_MODELS
    max_turns: int | None = None
    poll_interval_seconds: float = 1.0
    auto_save: bool = False
    display_mode: str = "final"
    save_raw_output: bool = True


def load_yaml_config(path: Path) -> dict[str, Any]:
    """Load a YAML mapping; return an empty mapping when the file is absent."""

    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError(f"Config must be a YAML mapping: {path}")
    return loaded


def load_debate_config(path: Path = DEFAULT_DEBATE_CONFIG_PATH) -> dict[str, Any]:
    return load_yaml_config(path)


def load_models_config(path: Path = DEFAULT_DEBATE_CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"{path} not found")
    return load_yaml_config(path)


def default_user_config_path() -> Path:
    """User-level fallback config, consulted only when the working dir has none."""

    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home) if config_home else Path.home() / ".config"
    return base / "debate" / "debate.yaml"


def resolve_models_config_path(args: argparse.Namespace) -> Path:
    """Resolve the config file: --models-config, project-local, then user-level.

    A `debate.yaml` in the working directory always wins, so a project can pin
    its own roster; the user-level copy is the fallback that lets `debate` run
    from any directory. Either way artifacts stay in the working directory.
    """

    raw_path = getattr(args, "models_config", None)
    if raw_path:
        if isinstance(raw_path, Path):
            return raw_path.expanduser()
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("models_config must be a non-empty path")
        return Path(raw_path.strip()).expanduser()

    if DEFAULT_DEBATE_CONFIG_PATH.exists():
        return DEFAULT_DEBATE_CONFIG_PATH
    user_config_path = default_user_config_path()
    if user_config_path.exists():
        return user_config_path
    return DEFAULT_DEBATE_CONFIG_PATH


def load_debate_state(path: Path = DEFAULT_DEBATE_STATE_PATH) -> dict[str, Any]:
    return load_yaml_config(path)


def save_debate_state(models: Sequence[str], path: Path = DEFAULT_DEBATE_STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"last_models": list(_resolve_models(models))}
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _known_state_models(raw_models: Any, debate_config: dict[str, Any]) -> tuple[str, ...]:
    """Last-used models, minus entries the worker registry no longer defines.

    Persisted state is a convenience memory, not an authority: after a roster
    change it can name workers that no longer exist, and keeping them would
    fail every later run at registry lookup.
    """

    if not isinstance(raw_models, (list, tuple)):
        return ()

    known = [_normalize_model_name(model) for model in raw_models]
    workers = debate_config.get("workers")
    if isinstance(workers, dict) and workers:
        known = [name for name in known if name == OFF_MODEL_NAME or name in workers]

    if sum(name != OFF_MODEL_NAME for name in known) < 2:
        return ()
    return tuple(known)


def resolve_debate_runtime_config(
    args: argparse.Namespace,
    debate_config: dict[str, Any],
    debate_state: dict[str, Any] | None = None,
) -> DebateRuntimeConfig:
    """Resolve debate config with CLI overrides applied last."""

    default_section = _mapping(debate_config.get("default"), "default")
    preset_section: dict[str, Any] = {}
    if getattr(args, "preset", None):
        presets = _mapping(debate_config.get("presets", {}), "presets")
        if args.preset not in presets:
            available = ", ".join(sorted(presets)) or "none"
            raise ValueError(
                f"Unknown debate preset '{args.preset}'. Available presets: {available}"
            )
        preset_section = _mapping(presets[args.preset], f"presets.{args.preset}")

    merged = {**default_section, **preset_section}

    state_section = debate_state or {}
    state_models = _known_state_models(state_section.get("last_models"), debate_config)
    models = _resolve_models(state_models or merged.get("models", BUILT_IN_MODELS))
    if getattr(args, "preset", None):
        models = _resolve_models(merged.get("models", models))
    if getattr(args, "models", None):
        models = _resolve_models(args.models.split(","))

    max_turns = merged.get("max_turns")
    if getattr(args, "max_turns", None) is not None:
        max_turns = args.max_turns

    return DebateRuntimeConfig(
        models=models,
        max_turns=_optional_positive_int(max_turns, "max_turns"),
        poll_interval_seconds=_positive_float(
            merged.get("poll_interval_seconds", 1.0), "poll_interval_seconds"
        ),
        auto_save=_bool(merged.get("auto_save", False), "auto_save"),
        display_mode=_choice(
            merged.get("display_mode", "final"), "display_mode", {"final", "live"}
        ),
        save_raw_output=_bool(merged.get("save_raw_output", True), "save_raw_output"),
    )


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"Debate config section '{name}' must be a mapping")
    return value


def _resolve_models(value: Any) -> tuple[str, ...]:
    if isinstance(value, tuple):
        raw_models = list(value)
    elif isinstance(value, list):
        raw_models = value
    else:
        raise ValueError("Debate models must be a list with two or three model names")

    models = [_normalize_model_name(model) for model in raw_models]
    if len(models) < 2 or len(models) > 5 or not all(models):
        raise ValueError("Debate models must contain between two and five non-empty model names")
    models = [OFF_MODEL_NAME if model.lower() == OFF_MODEL_NAME else model for model in models]
    active_models = [model for model in models if model != OFF_MODEL_NAME]
    if len(active_models) < 2:
        raise ValueError("Debate models must include at least two active models")
    while len(models) < 5:
        models.append(OFF_MODEL_NAME)
    return tuple(models)


def _normalize_model_name(value: Any) -> str:
    if value is False:
        return OFF_MODEL_NAME
    return str(value).strip()


def _optional_positive_int(value: Any, name: str) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer or null") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer or null")
    return parsed


def _positive_float(value: Any, name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive number") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive number")
    return parsed


def _bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ValueError(f"{name} must be true or false")


def _choice(value: Any, name: str, allowed: set[str]) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be one of: {', '.join(sorted(allowed))}")
    parsed = value.strip().lower()
    if parsed not in allowed:
        raise ValueError(f"{name} must be one of: {', '.join(sorted(allowed))}")
    return parsed
