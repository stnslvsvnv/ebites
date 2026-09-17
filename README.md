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
- The `opencode` CLI, configured with the `opencode-go` providers and the
  `closerouter` / `closerouter-anthropic` gateway entries

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

`debate.yaml` is the single config file: settings, presets, model workers, and
profiles in one place. A synced copy ships inside the skill directory
(`skills/debate/debate.yaml`, verified by test).

```yaml
default:
  models: ["deepseek-pro", "gpt-5.6-sol", "off"]
  max_turns: null
  display_mode: "final"

presets:
  fast:
    models: ["deepseek-flash", "glm-flash", "off"]
  strong:
    models: ["deepseek-pro", "gpt-5.6-sol", "off"]
  claude:
    models: ["claude", "claude-fable", "off"]
  council:
    models: ["deepseek-pro", "gpt-5.6-sol", "glm", "qwen", "off"]
```

`off` disables a slot. The `council` preset runs four agents. Use up to five
names in `--models`.

Workers are defined in the same file:

```yaml
workers:
  glm:
    description: "GLM-5.3 via opencode-go"
    launch: 'opencode run --model opencode-go/glm-5.3 "{{PROMPT}}"'
    prompt_file_launch: 'opencode run --pure --model opencode-go/glm-5.3 --file {{PROMPT_FILE}}'
```

- `{{PROMPT}}` is replaced with the prompt text. For workers that cannot take a
  huge argument, the runner generates a zsh wrapper script that reads the
  prompt file into a variable.
- `prompt_file_launch` (with `{{PROMPT_FILE}}`) is used when the CLI supports
  reading the prompt from stdin/redirection.
- The registry ships with eleven workers, all running through `opencode run`:
  `claude`, `claude-fable`, `gpt-5.6-sol`, and `gpt-5.6-luna` via the
  CloseRouter gateway, plus `deepseek-pro`, `deepseek-flash`, `glm`,
  `glm-flash`, `kimi-k3`, `qwen`, and `qwen-flash` via `opencode-go`.

## Run (TUI)

```zsh
debate
debate --models deepseek-pro,gpt-5.6-sol
debate --models deepseek-pro,gpt-5.6-sol,glm,qwen,claude
debate --preset council
debate --max-turns 5
debate --reasoning medium
debate --models-config /path/to/debate.yaml
```

Inside the TUI:

- `/new` starts a fresh debate and closes current agent sessions.
- `/models` opens the model picker (slots A-E).
- `/reasoning max|medium` switches reasoning effort for all agents (session-memory). `max` (default) = high effort; `medium` = quick debates. Every worker launches through `opencode run --variant`, so all of them honour the switch.
- `/save` writes `.debate/tmp/debate-{session_id}/transcript.md`.
- `Esc` pauses or resumes.
- `Ctrl+C` quits and cleans up agent sessions.

Typing a normal prompt after consensus or `max_turns` starts a new debate.

### Reasoning levels

Per-worker substitution (launch-time, no runtime switch):

| Worker family | `max` | `medium` |
|---------------|------|----------|
| every worker (`opencode run`) | `--variant max` | `--variant medium` |

## Run (headless)

For automation (e.g. 5A): run the debate to consensus or `max_turns` without a
TUI and write the transcript to a fixed path.

```zsh
debate --headless \
  --models claude-fable,deepseek-pro,gpt-5.6-sol \
  --max-turns 8 \
  --reasoning medium \
  --prompt-file debate/prompt.md \
  --transcript-out debate/transcript.md \
  --turn-timeout 300
```

`--reasoning medium` runs quick debates (lower effort per agent).

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
