# debate

`debate` is a tiny TUI that lets two AI CLI agents argue through a task while you watch, interrupt, switch models, and save the transcript.

The repository is named `ebites`; the tool is `debate`.

## Why

Copying text between two terminal windows is boring. `debate` runs both agents in managed `tmux` sessions, feeds each agent the original prompt plus the full conversation history, and stops when an agent reaches consensus.

By default the UI shows only each agent's final answer. Raw command output is still captured on disk when you want to inspect the full run.

## Install

Requirements:

- Python 3.10+
- `tmux`
- At least one configured AI CLI, such as `codex`, `opencode`, or `claude`

From a checkout:

```zsh
python3 -m pip install -e '.[dev]'
debate
```

Or run without installing:

```zsh
./debate
```

## Configure

Edit `debate.yaml` to choose defaults:

```yaml
models_config: "models.yaml"

default:
  models: ["deepseek-pro", "codex"]
  max_turns: null
  display_mode: "final"
```

Edit `models.yaml` to define model launch lines:

```yaml
workers:
  codex:
    description: "Codex CLI"
    launch: 'codex exec "{{PROMPT}}"'
```

`{{PROMPT}}` is replaced with `$(cat path/to/prompt.txt)` for each turn. Keep launch lines one-line shell commands.

## Run

```zsh
debate
debate --models codex,deepseek-pro
debate --preset fast
debate --profile codex
debate --max-turns 5
debate --models-config /path/to/models.yaml
```

Inside the TUI:

- `/new` starts a fresh debate and closes current agent sessions.
- `/models` opens a two-column model picker.
- `/save` writes `.debate/tmp/debate-{session_id}/transcript.md`.
- `Esc` pauses or resumes.
- `Ctrl+C` quits and cleans up agent sessions.

Typing a normal prompt after consensus or `max_turns` starts a new debate.

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

When an agent starts its final answer with `CONSENSUS:`, debate stops, the UI shows a consensus banner, and the agent `tmux` sessions are killed so no background processes keep eating memory.

## Develop

```zsh
python3 -m pip install -e '.[dev]'
pytest -q
ruff check src tests
```

## debate

```text
.:=+++++++++++======+++++++++*****++++=+======++++===......................
.*###########**********######%%%%%###****+++****#****=................::::..
.*#######*****+++++*********##%%####****+++++********+=:................:::.
.******#***********++******####%#*****++++++++********+=:...................
.+***+*##%%%%%%%#********##***###*****++++++++++*****+++=...................
.=+++++*#%@@@@@@@@%########****##****++++++**#*******++++:..................
.=====++++*%@@@@@@@@@%%%##*******+++******##%%%%%#***++++-..................
.======++++*#%@@@@@@@%%%#*********+*+**##%@@@@@%#*++++++=-..................
.======+++++**###########***##******++**#%@@@%%#*++++++==:..................
.====+++++++++++++++******++********++***#####**++++++==-...................
.=++=++=====+++++===++++++:.-++.-+=*--+-:+*+==++=--=+===. ..................
.+++++++===--============+: -+= -- +: +: =- -..+.:--===:.  .................
.++++++===----===========+: -+= -- =: +: +: --:+-:::==:.    ................
.+++++====================:.:-=.--:+:.+-.==:-:-=:-::=-.      ...............
.++++++=========--==========++********++++++==+=====:.        ..............
.+**++++========-===========+++**#####**++++==--==-.    .     ..............
.+*****+++=======-==========+++**#####***++===:==:.     .     ..............
.+*******+========--==--===++**##%%%%%##**++==:::.      .     ..............
.+********+++=============+*#%%%%%%@%%%%%%%*++-.               .............
.+**+++++++++============+#@@@@%%%@@@@@@@@@%*+-.     ........... ...........
.=================+++===+*%@@@@@@@@@@@@@@@@%#+-.....::----------:...........
.=====+============++++++*%@@@@@@@@@@@@@@@@@%+:.:------==---======-::.......
.=++++++=============+****#%@@@@@@@@@@@@@@@@#+-=+====-==+============--::...
.=++++++++========+==++####%%@@@##@@#%@@@@%#*+**+======+*+========+=====--:.
.+++++=+++========+*#*+*%%%@@@@#*+****@@@%#++**++*=====++++*++===++*+===++=.
.+++++=+*++====+++****+******##*==++#*#%%%#***+*+**====+*+****+=+++#*===+++.
.********#%*++*##**************#++++*#%@@@%##*+++##************=+=+##+==+*+.
.*#######%%%##%%%****************##+*%@@@@@@@#*###********####*==+*%#*+++*+.
.%@@@@%%%%@@%%@@@#**#%%#####****####%@@@@@@@@@%##**###########*+**#@%****#+.
.@@@@@@@@@@@@@@@@#*%@@@@@@@@%**#%##%%%%%%%%@@@@%%**#@@@@@@@%###%#*+***####-.
.@@@@@@@@@@@@@@@@++%@@@@@@@%%+**#**************##**#%%%%%%%%**#%%*-...:*%*..
.%@@@@@@@@@%%%%%%**#%%%######*+**##****************#########**####*+-::=#=:.
.#############***************+++++++****+*****++++++**********++==+=======:.
.*#######*****************++++++************++++++===========-:::::::::::::.
.*#######*****************+++++++++++++++++++++++=====---:::::.......:::::..
.:=========================================--------------:::..:............
```
