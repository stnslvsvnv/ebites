# debate (ebites)

`debate` is a TUI + headless CLI that lets two to five AI CLI agents argue
through a task while you watch, interrupt, switch models, and save the
transcript. The repository is named `ebites`; the tool is `debate`.

## Why

Copying text between terminal windows is boring. `debate` runs each agent in a
managed `tmux` session, feeds every agent the original prompt plus the full
conversation history, and stops when an agent reaches consensus. It supports
2-5 agents (slots A-E), structured consensus answers, and a headless mode for
pipeline/automation use (e.g. the 5A method).

## Install

Requirements:

- Python 3.10+
- `tmux`
- `zsh` (wrapper scripts for non-stdin workers)
- At least one configured AI CLI, such as `codex`, `opencode`, or `claude`

From a checkout, inside a project venv (recommended — lets `debate` reuse the
project's model/provider configuration):

```zsh
/path/to/project/.venv/bin/pip install -e '.[dev]'
/path/to/project/.venv/bin/debate --help
```

Standalone venv:

```zsh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/debate
```

## Configure

Edit `debate.yaml` to choose defaults:

```yaml
models_config: "models.yaml"

default:
  models: ["deepseek-pro", "codex", "off"]
  max_turns: null
  display_mode: "final"

presets:
  fast:
    models: ["deepseek-flash", "glm", "off"]
  strong:
    models: ["deepseek-pro", "codex", "off"]
  claude:
    models: ["claude", "claude", "off"]
  council:
    models: ["deepseek-pro", "codex", "glm", "qwen", "off"]
```

`off` disables a slot. The `council` preset runs four agents. Use up to five
names in `--models`.

Edit `models.yaml` to define model launch lines:

```yaml
workers:
  codex:
    description: "Codex CLI"
    launch: 'codex exec "{{PROMPT}}"'
    prompt_file_launch: 'codex exec < {{PROMPT_FILE}}'
```

- `{{PROMPT}}` is replaced with the prompt text. For workers that cannot take a
  huge argument, the runner generates a zsh wrapper script that reads the
  prompt file into a variable.
- `prompt_file_launch` (with `{{PROMPT_FILE}}`) is used when the CLI supports
  reading the prompt from stdin/redirection.
- The registry ships with `claude-fable` (canonical Claude slot, runs through
  the `claude` binary, never through OpenCode), `codex`, `deepseek-pro`,
  `glm`, `qwen`, `deepseek-*`, and 9router aliases.

## Run (TUI)

```zsh
debate
debate --models codex,deepseek-pro
debate --models deepseek-pro,codex,glm,qwen,claude
debate --preset council
debate --max-turns 5
debate --models-config /path/to/models.yaml
```

Inside the TUI:

- `/new` starts a fresh debate and closes current agent sessions.
- `/models` opens the model picker (slots A-E).
- `/save` writes `.debate/tmp/debate-{session_id}/transcript.md`.
- `Esc` pauses or resumes.
- `Ctrl+C` quits and cleans up agent sessions.

Typing a normal prompt after consensus or `max_turns` starts a new debate.

## Run (headless)

For automation (e.g. 5A): run the debate to consensus or `max_turns` without a
TUI and write the transcript to a fixed path.

```zsh
debate --headless \
  --models claude-fable,deepseek-pro,codex \
  --max-turns 8 \
  --prompt-file debate/prompt.md \
  --transcript-out debate/transcript.md \
  --turn-timeout 300
```

Exit codes:

- `0` — consensus reached or max turns exhausted (transcript written)
- `2` — an agent produced no usable output (crash/timeout)
- `3` — configuration error (missing prompt-file/transcript-out, unknown model)

Headless mode does not read or write `state.yaml`, so automation always gets
the exact model list it asked for.

## Output

Saved transcripts include:

- the initial prompt
- every final agent answer
- every user intervention
- timestamps and selected model names

Raw output, when enabled, is saved under `.debate/tmp/debate-{session_id}/raw/`.

## How It Works

Each agent gets a turn prompt containing:

- the original task
- all final answers so far
- all user interventions
- instructions to wrap the final answer in `<DEBATE_FINAL>...</DEBATE_FINAL>`

When an agent starts its final answer with `CONSENSUS:`, debate stops, the UI
shows a consensus banner, and the agent `tmux` sessions are killed so no
background processes keep eating memory. Consensus answers carry a structured
rationale: Decision, Key arguments, Residual risks/trade-offs, Next step.
Consensus is only accepted after every active agent has contributed at least
one turn.

## Develop

```zsh
python3 -m pip install -e '.[dev]'
pytest -q
ruff check src tests
```
