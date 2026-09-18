---
name: debate
description: Run multi-agent AI debates (2-5 agents, TUI or headless) via the ebites debate CLI; use for architecture consensus, independent opinions, plan validation, or review debates across model families.
license: MIT
compatibility: opencode
metadata:
  install: npx skills add stnslvsvnv/ebites@debate -g -y
---

## What I do

Runs the `debate` CLI from the ebites repository: two to five AI agents
(Claude, DeepSeek, GPT, GLM, Kimi, Qwen) argue over a prompt in managed tmux
sessions until consensus or max turns, then save a transcript.

## When to use me

- Architecture decisions needing cross-model consensus
- Validating a plan with independent reviewers
- Any "ask several models and compare" task

## How to run

The CLI must be installed (see repo README): `debate` on PATH, or
`<venv>/bin/debate`. The single config file is `debate.yaml` (settings,
presets, model workers, profiles); this skill ships a synced copy so agents
can inspect the exact worker registry and presets without cloning the repo.
Use `--models-config <path/to/debate.yaml>` to point at a specific copy.

TUI:

```zsh
debate --preset council
```

Headless (automation):

```zsh
debate --headless \
  --models claude-fable,deepseek-pro,gpt-5.6-sol \
  --max-turns 8 \
  --prompt-file prompt.md \
  --transcript-out transcript.md
```

Exit 0 = consensus or max turns; 2 = agent crash/timeout; 3 = config error.

Levels (preset + headless in one flag):

```zsh
debate --low  --prompt-file prompt.md --transcript-out transcript.md
debate --high --prompt-file prompt.md --transcript-out transcript.md
```

`--low` = DeepSeek V4.1 Flash + GLM-5.3 Flash + Qwen3.8 Flash; `--high` =
Claude Opus 5 + GPT-5.6 Sol + GLM-5.3. Both read their model list from the
`low`/`high` presets in `debate.yaml`. Artifacts land in the directory you run
`debate` from.

## Rules

- Every worker runs through `opencode run` except `gpt-5.6-sol`, which runs
  through the `codex` CLI with the `closerouter` model provider.
  `claude-fable` is advertised on the Anthropic messages wire only, so it needs
  the `closerouter-anthropic` provider; the `claude` binary and 9router are no
  longer used.
- `--low`/`--high` imply `--headless` and reject `--models`/`--profile`/`--preset`.
- Headless mode ignores `state.yaml`; always name the roster explicitly, via
  `--models`, `--preset`, or a level flag.
- Read the transcript and summarize the consensus (Decision / Key arguments /
  Residual risks / Next step) for the caller.
