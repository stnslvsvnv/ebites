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
(Claude Fable, DeepSeek, Codex, GLM, Qwen) argue over a prompt in managed
tmux sessions until consensus or max turns, then save a transcript.

## When to use me

- Architecture decisions needing cross-model consensus
- Validating a plan with independent reviewers
- Any "ask several models and compare" task

## How to run

The CLI must be installed (see repo README): `debate` on PATH, or
`<venv>/bin/debate`. Model workers live in `models.yaml`.

TUI:

```zsh
debate --preset council
```

Headless (automation):

```zsh
debate --headless \
  --models claude-fable,deepseek-pro,codex \
  --max-turns 8 \
  --prompt-file prompt.md \
  --transcript-out transcript.md
```

Exit 0 = consensus or max turns; 2 = agent crash/timeout; 3 = config error.

## Rules

- `claude-fable` must run through the `claude` binary, never OpenCode.
- `claude-opus`/`opus` is forbidden in 5A debate context.
- Headless mode ignores `state.yaml`; always pass `--models` explicitly.
- Read the transcript and summarize the consensus (Decision / Key arguments /
  Residual risks / Next step) for the caller.
