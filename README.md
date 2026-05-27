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


!!!!~~~~~!!!77???7?7777!!!!~~~~^^^^~~!!7777??JJ??77!!7???5&&&&&&&&&&&&###########
!!~!~~!!!!777????????7777!!!!!~^^^~~!!777??JJJJ??777!77??J5#&&&&&&&&&&&&&########
!!!!!!!7777?????????????7777!!!~^^~!!!7????JJYJJ?7!!!777??JYB&&&&&&&&&&&&&&&#####
77777!!!!!!!~!!7777?????7!!!7!!!~~!7777??JJJJJJJJ??7777?7??J5#@&&&&&&&&&&&&&&####
J??JJ7!^:::.....:~!77777!!!!7777!!!77???JJJJJJ??????777???JJJP&@&&&&&&&&&&&&&&&&#
YYYYJJ?7!:   ..   :^~~!~~~~!77?7!!7???????????7!~!!!!777??JJJ5&@@@@&&&&&&&&&&&&&#
YYYYYJJJJ7^.  ..    .:^^~~!77??7777JJ???77!!!~^::::^~7????JJYYB@@@@@@&&&&&&&&&&&&
YYYYYYJJJJ?!^........::^~!7777777???????77~^:...:..~7?JJJJJYY5B@@@@@@@@&&&&&&&&&&
YYYYYYYJJ????!!~~~~!!!!~!!777!!77????????7!^:...::~7?JJJJJYYYP&@@@@@@@@@&&&&&&&&&
YYYYYYYYJJJJ??????JJ?77777??777!!77777?J?7!!~~~~~77?JJJYYYYY5#@@@@@@@@@@@&&&&&&&&
YYYYJYYYYYYJJJJJJYYYYJJJJJJ#&BP7##J??75GJYGP77?J?7?JY55YYYY5G&@@@@@@@@@@@@&&&&&&&
JJYYYYYY555555Y55555YYYYYYJ&@PJ7&@5&&JB@5P@&JP@G#&JG@PBBYY5P&@@@@@@@@@@@@@@@&&&&&
JJJJJJY55PPPPP555555YY55YYY&@B57&@?#@YB@5Y@#J#@GB#55#BBPY5P#@@@@@@@@@@@@@@@@@&&&&
JJJJYY55PPPPP555555555555YY&@GY?@@J&@JB@PY@&JB@PG&5P#5&@55#@@@@@@@@@@@@@@@@@@&&&&
JJJJYY5555555555PPP55555555GGGP?55JP5?5PY?5PYYPPG5Y5PPGP5B@@@@@@@@@@@@@@@@@@@@&&&
???JJJYY5555555PPP5555555PP5YJJ?777!!7777???JYJYYY55JYYP#@@@@@@@@@@@@@@@@@@@@@&&&
?????JJYY555555P55555555PP555YJJ??7!~~!!!???JYYY5GG5YPB&@@@@@@@@@@@@@@@@@@@@@@@&&
???????JYYYYYY55PPPP555555YYYJJ??7!~~~~~!77?JYYY5GGYP#@@@@@@@@@@@@@@@@@@@@@@@@@&&
????????J55YYY5Y55PPP5PPP55YJ??!!~^^^^^^~!!77?JY5G#B&@@@@@@@@@@@@@@@@@@@@@@@@@@&&
777777777JJJY55YY555PP555YYJ7!^::::::::::^^^^~7JYP&@@@@@@@@@@@@@@@@@@@@@@@@@@@&&&
77???J????JJY55YYYYY55P5YYJ~.::::::::........:^!?5#@@@@@@@@&&&&&&&@@@@@@@@@@@@@&&
Y555555555555555YYJYYY5YYY!:.::.:::::.........:!?5#@@@@@&BBGGPGGGGGGB#&&@@@@@@&&&
5555YY55555555555YYJJJJJYJ!:...................^?P&###BGPPPPPPPPPP55555GB##&&&&&&
YYYYYJY5555555Y55555Y?????7^:..... ............~JGB5555PPPP5Y5555555555Y5PPGB##&&
JJJJJJJJYYYYYYYYYYY55Y?7!!!~^:....:.  :.......^!J5?7YY55555Y??Y55555555YY5555PGB#
JJJJYJJ?JY5Y555YYYJJ??J7~~^^::...~J7^~7?.....~???!7JJJ55555J??Y55YY55YYY?JYYYYYYP
JJJJYYJJJY5555Y5JJ??!7J?!^^:::::!?JJJJ77^.:::!?J7???!!5555YJ?JJ7!??YYYYJ?!JYYYYJJ
JJJJYJ7?7?YYYYJ???777??????????!~Y55YJ!7!::::7?7!J?JJ77JYYYJ???77777YYJYJ^!J55YJJ
777!7!~!^^!?J7!~!?77777777??????!!??JJ?!^:...^~!7?JJ7~!777777!!!!!!!JYYY?^~?YYYJ?
!~~!!~^~:::^~^^::7777777777??777?77!!J?:........^!!!!!7!!!!!!!!!!!!!JYY?!^~7JJJ?7
...::::^:..::.. .!7!~^^~!!!!!77777!~~~^:.........:^!!!!!!!!~~~~~~!!!???7^:~7777!7
     .........  .!!^:........^77!~^^^^^:::::::.....^~7!!:.......:!!~^~!77~!!!~~~J
        .....   ^J7^.......::^??!~!777!!!!!!!!!!!!!~~77~:::::::.:!7!:^!J#&#BG?^^Y
................~??~:::::^^^^^?J??~!!!!!!!!!!!777?77??7!~~~~~~~~!??7^^~~?PB##B!7G
~~~~~~~~~~~~!!!!!777!77777777??????????????????????????77777777777777???7?555J??Y
!!!!!!!!!!!!77777777777777?????J?????????????????JJJJJJJJJJJJJJJJJY5PPGGGGGGGGGGB
!!!!!!!!77777777?????????????JJJJ????????????????JJJYYY5555PPPGGBBB##############
!!!!!!77777777777?????????????JJJJJJJJJJJJJJJJJJJYYYYYY555PPGGB####&&&###########
