#!/usr/bin/env python3
"""CLI entrypoint for AI Debate"""

import argparse
import sys
from typing import Any

from .config import (
    DebateRuntimeConfig,
    load_debate_config,
    load_debate_state,
    load_models_config,
    resolve_debate_runtime_config,
    resolve_models_config_path,
    save_debate_state,
)
from .orchestrator import DebateOrchestrator
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
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve CLI model/profile options into two worker launch configs."""

    if getattr(args, "models", None):
        model_names = [model.strip() for model in args.models.split(",")]
        if len(model_names) != 2 or not all(model_names):
            print("Error: --models must specify exactly 2 models (e.g., codex,deepseek-pro)")
            sys.exit(1)
        model_a_name, model_b_name = model_names
    elif args.profile:
        if args.profile not in models_yaml["profiles"]:
            print(f"Error: Profile '{args.profile}' not found")
            print(f"Available profiles: {', '.join(models_yaml['profiles'].keys())}")
            sys.exit(1)

        profile = models_yaml["profiles"][args.profile]
        model_a_name = profile["investigation"]
        model_b_name = profile["implementation"]
    else:
        model_a_name, model_b_name = runtime_config.models

    return get_model_config(models_yaml, model_a_name), get_model_config(models_yaml, model_b_name)


def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description="AI Debate - Two agents discussing any topic",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  debate\n"
            "  debate --preset fast\n"
            "  debate --models codex,deepseek-pro\n"
            "  debate --models claude-opus,glm --max-turns 5\n"
            "  debate --profile claude\n\n"
            "Defaults come from debate.yaml"
        ),
    )

    parser.add_argument(
        "--models",
        type=str,
        help="Comma-separated model names for Agent A and Agent B (e.g., codex,deepseek-pro)",
    )

    parser.add_argument("--profile", type=str, help="Use a profile from models config")

    parser.add_argument("--preset", type=str, help="Use a preset from debate.yaml")

    parser.add_argument(
        "--max-turns", type=int, help="Maximum number of turns (default: unlimited until consensus)"
    )

    parser.add_argument(
        "--models-config",
        type=str,
        help="Path to models.yaml registry (default: models.yaml or models_config in debate config)",
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
        sys.exit(1)

    model_a_config, model_b_config = resolve_model_selection(args, models_yaml, runtime_config)
    save_debate_state((model_a_config["name"], model_b_config["name"]))

    # Create orchestrator
    orchestrator = DebateOrchestrator(
        model_a_config=model_a_config,
        model_b_config=model_b_config,
        max_turns=runtime_config.max_turns,
        poll_interval_seconds=runtime_config.poll_interval_seconds,
        auto_save=runtime_config.auto_save,
        display_mode=runtime_config.display_mode,
        save_raw_output=runtime_config.save_raw_output,
    )

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
