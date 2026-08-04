#!/usr/bin/env python3
"""CLI entrypoint for AI Debate"""

import argparse
import sys
from pathlib import Path
from typing import Any

from .config import (
    OFF_MODEL_NAME,
    DebateRuntimeConfig,
    load_debate_config,
    load_debate_state,
    load_models_config,
    resolve_debate_runtime_config,
    resolve_models_config_path,
    save_debate_state,
)
from .orchestrator import DebateOrchestrator, prune_unsaved_debate_tmp
from .tui import run_tui


def get_model_config(models_yaml: dict[str, Any], model_name: str) -> dict[str, Any]:
    """Get model configuration by name"""
    if model_name not in models_yaml["workers"]:
        print(f"Error: Model '{model_name}' not found in models config")
        print(f"Available models: {', '.join(models_yaml['workers'].keys())}")
        sys.exit(1)

    config = dict(models_yaml["workers"][model_name])
    config["name"] = model_name
    return config


def resolve_model_selection(
    args: argparse.Namespace,
    models_yaml: dict[str, Any],
    runtime_config: DebateRuntimeConfig,
) -> tuple[
    dict[str, Any] | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
]:
    """Resolve CLI model/profile options into three model slots."""

    if getattr(args, "models", None):
        model_names = _normalize_model_names(args.models.split(","))
        if model_names is None:
            print(
                "Error: --models must specify 2 to 5 models "
                "(e.g., codex,deepseek-pro or codex,deepseek-pro,glm,qwen,off)"
            )
            sys.exit(1)
    elif args.profile:
        if args.profile not in models_yaml["profiles"]:
            print(f"Error: Profile '{args.profile}' not found")
            print(f"Available profiles: {', '.join(models_yaml['profiles'].keys())}")
            sys.exit(1)

        profile = models_yaml["profiles"][args.profile]
        model_names = _normalize_model_names(
            [
                profile["investigation"],
                profile["implementation"],
                profile.get("review", OFF_MODEL_NAME),
            ]
        )
    else:
        model_names = _normalize_model_names(runtime_config.models)

    if model_names is None:
        print("Error: Debate requires at least two active models")
        sys.exit(1)

    configs = [_optional_model_config(models_yaml, model_name) for model_name in model_names]
    return configs[0], configs[1], configs[2], configs[3], configs[4]


def _normalize_model_names(raw_models: Any) -> tuple[str, str, str, str, str] | None:
    model_names = [_normalize_model_name(model) for model in raw_models]
    if len(model_names) < 2 or len(model_names) > 5 or not all(model_names):
        return None
    normalized = [
        OFF_MODEL_NAME if model.lower() == OFF_MODEL_NAME else model for model in model_names
    ]
    if sum(model != OFF_MODEL_NAME for model in normalized) < 2:
        return None
    while len(normalized) < 5:
        normalized.append(OFF_MODEL_NAME)
    return normalized[0], normalized[1], normalized[2], normalized[3], normalized[4]


def _normalize_model_name(value: Any) -> str:
    if value is False:
        return OFF_MODEL_NAME
    return str(value).strip()


def _optional_model_config(models_yaml: dict[str, Any], model_name: str) -> dict[str, Any] | None:
    if model_name == OFF_MODEL_NAME:
        return None
    return get_model_config(models_yaml, model_name)


def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description="AI Debate - Two or three agents discussing any topic",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  debate\n"
            "  debate --preset fast\n"
            "  debate --models codex,deepseek-pro\n"
            "  debate --models claude-opus,glm,off --max-turns 5\n"
            "  debate --profile claude\n\n"
            "Defaults come from debate.yaml"
        ),
    )

    parser.add_argument(
        "--models",
        type=str,
        help=(
            "Comma-separated model names for Agent A/B/C; use off to disable a slot "
            "(e.g., codex,deepseek-pro,off)"
        ),
    )

    parser.add_argument("--profile", type=str, help="Use a profile from models config")

    parser.add_argument("--preset", type=str, help="Use a preset from debate.yaml")

    parser.add_argument(
        "--max-turns", type=int, help="Maximum number of turns (default: unlimited until consensus)"
    )

    parser.add_argument(
        "--models-config",
        type=str,
        help="Path to the single debate config file (default: debate.yaml)",
    )

    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run without TUI; requires --prompt-file and --transcript-out",
    )

    parser.add_argument(
        "--prompt-file",
        type=str,
        help="Path to the initial prompt file (headless mode)",
    )

    parser.add_argument(
        "--transcript-out",
        type=str,
        help="Path where the transcript is written (headless mode)",
    )

    parser.add_argument(
        "--turn-timeout",
        type=float,
        default=300.0,
        help="Per-turn timeout in seconds (headless mode, default: 300)",
    )

    return parser.parse_args()


def main():
    """Main entrypoint"""
    args = parse_args()
    try:
        debate_config = load_debate_config()
        debate_state = load_debate_state()
        runtime_config = resolve_debate_runtime_config(args, debate_config, debate_state)
        models_config_path = resolve_models_config_path(args, debate_config)
        models_yaml = load_models_config(models_config_path)
    except ValueError as exc:
        print(f"Error: {exc}")
        sys.exit(3)

    agent_model_configs = resolve_model_selection(args, models_yaml, runtime_config)
    selected_model_names = tuple(
        config["name"] if config is not None else OFF_MODEL_NAME for config in agent_model_configs
    )
    prune_unsaved_debate_tmp()

    # Create orchestrator
    orchestrator = DebateOrchestrator(
        agent_model_configs=agent_model_configs,
        max_turns=runtime_config.max_turns,
        poll_interval_seconds=runtime_config.poll_interval_seconds,
        auto_save=runtime_config.auto_save,
        display_mode=runtime_config.display_mode,
        save_raw_output=runtime_config.save_raw_output,
    )

    if args.headless:
        from .headless import EXIT_AGENT_FAILURE, load_prompt_file, run_headless

        if not args.prompt_file or not args.transcript_out:
            print(
                "Error: --headless requires --prompt-file and --transcript-out",
                file=sys.stderr,
            )
            sys.exit(3)
        prompt = load_prompt_file(Path(args.prompt_file))
        status = run_headless(
            orchestrator,
            prompt=prompt,
            transcript_out=Path(args.transcript_out),
            poll_interval_seconds=runtime_config.poll_interval_seconds,
            turn_timeout_seconds=args.turn_timeout,
        )
        if status == EXIT_AGENT_FAILURE:
            print("Debate failed: agent produced no usable output", file=sys.stderr)
        sys.exit(status)
    else:
        save_debate_state(selected_model_names)

    # Run TUI
    try:
        run_tui(orchestrator, models_yaml=models_yaml)
    except KeyboardInterrupt:
        print("\nDebate interrupted by user")
        orchestrator.cleanup()
    except Exception as e:
        print(f"\nError: {e}")
        orchestrator.cleanup()
        raise


if __name__ == "__main__":
    main()
