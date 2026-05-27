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




!!!~~~~!!77?????77!!!!~~^^^~~!!777?JJ??7!!7??5&&&&&&&&&&########
!!!!~!!77??J?????777!!!!~^^~!!77??JJJ??7!!77??5#&&&&&&&&&&&#####
7777!!7777777??????7!!!!!~~!777??JJJJJJ?77777?JY#@&&&&&&&&&&&###
???J7~:::...:~!7777!!!777!!!7???JJJJJ????77???JJP@@&&&&&&&&&&&&#
YYYYJ?7^   .  .^~~~~~!7?7!!7????????7~^~!!7???JJ5&@@@&&&&&&&&&&&
YYYYJJJ?~:.     .:^~!77?777?J??77!~^:.:.:~???JJYY#@@@@@&&&&&&&&&
YYYYYJJJ?7!^^^^^~~~~777!77???????!^...::!?JJJJYYP&@@@@@@&&&&&&&&
YYYYYYJJJJ????JJ?777????777777?J77!~^^~7??JJYYY5B@@@@@@@@@&&&&&&
YYYYYYYYYYYYYYYYYJYYJG@G?G&Y5?PGJ#G?YY5JJPPPYYYP&@@@@@@@@@@&&&&&
JJJYYY5PPP555555YYYYYB@G?B&5@5B&J@BY@B&B5&GBYYP#@@@@@@@@@@@@&&&&
JJJYY5PPPP5555555555YB@P?B&5@5#&J@B5@GBG5BG&P5#@@@@@@@@@@@@@@&&&
JJJJY5555555PPP555555PGPYY5Y5?Y5J5PY5PP55PPPP#@@@@@@@@@@@@@@@@&&
???JJY555555P5555555P5YJJ?77!!!7???JYY555YYG&@@@@@@@@@@@@@@@@@&&
?????JYYYYY5PPP5555555YYJ?7!~~~~7??JYYPG55#@@@@@@@@@@@@@@@@@@@&&
??????JYYYY555PP5PP55YJ?7!~~^^^^!77?JY5BB#@@@@@@@@@@@@@@@@@@@@@&
7777777?JY55YY55PP55YJ7~^:::::::::^^!?YB@@@@@@@@@@@@@@@@@@@@@@&&
JJJYYYYJYYY5YYYYY55YJ~.::::::.......:!JG@@@@@&#######&&@@@@@@@&&
5555555555555YYJJYYY?^...............~JB&&&#BGPPPPPPPPPGB#&&&&&&
YYYYJ55555YY5555J????~:...  .........^YBP555PPP5555555555PPGB#&&
JJJJJJYYY5YYYYY55J7!!~^:. :^..^:....^!J?7Y55555J?5555555YYY55PGB
JJJYJJJY5555YYJ?!??~^:::.:!J77?!...^?J77J?J555Y??J?JJYYYJ?JYYYYY
JJJYJ???Y5YJJ?777??777!!!!JYYJ!7^::~?77???7JYYYJ??777JYYY7~J55YJ
?777!!~^7??!~777777??????77?JJ?~:..:~!7?J7~!77777!!!!7YYY7^7YYJ?
~~~~^^^.:^^:.~77777777?77?7!!?7.......^!!!!!!!!!!!!!!7YJ?~^7JJ?7
.  ..::..:.  ~7~:.:^^^^!77~~~^::...... .^!7!~^^:::^~!!!7!^^!!!!7
      .....  77^.......~?7~!!!!!!!~~~~~~~!7!:......~7~:~JBBG5!^J
.........::::7?~::^^^^^~J?7!!!!!!777777?77?!~~~!~~~7?!^~!JG##Y~5
~~~~~~!!!!!!!777777777???J?????????????????????77777??JYJJY55YY5
!!!!!!!7777777?777??????J?????????????JJJYYYYY5555PPGBBBB##B####
!!!!!77777777??????????JJJJJJJ??JJJJJJJYYYY555PGGB###&&#&#######
