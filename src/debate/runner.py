#!/usr/bin/env python3
"""Model runner for debate agents."""

from __future__ import annotations

import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class ModelProcess:
    """A single agent turn running inside a persistent tmux session."""

    session_name: str
    prompt_file: Path
    start_marker: str
    sentinel: str
    command: str


class TmuxClientProtocol(Protocol):
    def has_session(self, session_name: str) -> bool: ...

    def new_session(self, session_name: str, cwd: Path) -> None: ...

    def set_history_limit(self, session_name: str, limit: int) -> None: ...

    def send_command(self, session_name: str, command: str) -> None: ...

    def send_ctrl_c(self, session_name: str) -> None: ...

    def clear_history(self, session_name: str) -> None: ...

    def capture(self, session_name: str) -> str: ...

    def kill_session(self, session_name: str) -> None: ...


class TmuxClient:
    """Thin tmux adapter kept separate so runner behavior stays unit-testable."""

    def has_session(self, session_name: str) -> bool:
        result = subprocess.run(["tmux", "has-session", "-t", session_name], capture_output=True)
        return result.returncode == 0

    def new_session(self, session_name: str, cwd: Path) -> None:
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", session_name, "-c", str(cwd)], check=True
        )

    def set_history_limit(self, session_name: str, limit: int) -> None:
        subprocess.run(
            ["tmux", "set-option", "-t", session_name, "history-limit", str(limit)], check=True
        )

    def send_command(self, session_name: str, command: str) -> None:
        subprocess.run(["tmux", "send-keys", "-t", session_name, command, "C-m"], check=True)

    def send_ctrl_c(self, session_name: str) -> None:
        subprocess.run(["tmux", "send-keys", "-t", session_name, "C-c"], capture_output=True)

    def clear_history(self, session_name: str) -> None:
        subprocess.run(["tmux", "clear-history", "-t", session_name], capture_output=True)

    def capture(self, session_name: str) -> str:
        try:
            result = subprocess.run(
                ["tmux", "capture-pane", "-p", "-J", "-S", "-", "-t", session_name],
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError:
            return ""
        return result.stdout

    def kill_session(self, session_name: str) -> None:
        subprocess.run(["tmux", "kill-session", "-t", session_name], capture_output=True)


class ModelRunner:
    """Run a configured worker launch template inside tmux."""

    def __init__(
        self,
        model_config: dict[str, Any],
        *,
        tmux_client: TmuxClientProtocol | None = None,
        session_root: Path | str = Path(".debate/tmp"),
        cwd: Path | str | None = None,
        reasoning_level: str = "max",
    ):
        if "launch" not in model_config:
            raise ValueError("Model config must contain a 'launch' template")

        self.name = model_config.get("name", model_config.get("model", "unknown"))
        self.launch_template = model_config["launch"]
        self.prompt_file_launch_template = model_config.get("prompt_file_launch")
        self.description = model_config.get("description", self.name)
        self.tmux = tmux_client or TmuxClient()
        self.session_root = Path(session_root)
        self.cwd = Path.cwd() if cwd is None else Path(cwd)
        self.reasoning_level = reasoning_level

    def supports_reasoning(self) -> bool:
        """True when this worker's launch template can be retuned for reasoning level."""

        launch = self.launch_template
        return any(
            marker in launch
            for marker in ("--variant ", "--effort ", 'model_reasoning_effort="')
        )

    def set_reasoning_level(self, level: str) -> None:
        if level not in {"max", "medium"}:
            raise ValueError(f"reasoning level must be 'max' or 'medium', got: {level!r}")
        self.reasoning_level = level
        if not self.supports_reasoning() and level != "max":
            return

    @staticmethod
    def _apply_reasoning(launch_str: str, level: str) -> str:
        """Substitute reasoning level markers in a launch template string.

        Markers handled (per worker family):
        - opencode `--variant <x>`     -> `--variant <level>` (max|medium)
        - claude `--effort <x>`        -> `--effort <level>` (max|medium)
        - codex  `model_reasoning_effort="<x>"` -> `model_reasoning_effort="<codex_level>"`
          (max -> high, medium -> medium)
        """

        if not launch_str:
            return launch_str

        # opencode --variant <x>
        launch_str = re.sub(r"--variant\s+\S+", f"--variant {level}", launch_str)

        # claude --effort <x>
        launch_str = re.sub(r"--effort\s+\S+", f"--effort {level}", launch_str)

        # codex model_reasoning_effort="..."
        codex_effort = "high" if level == "max" else level
        launch_str = re.sub(
            r'model_reasoning_effort="\w+"',
            f'model_reasoning_effort="{codex_effort}"',
            launch_str,
        )
        return launch_str

    @property
    def model(self) -> str:
        """Backward-compatible display field used by transcript generation."""

        return self.name

    def run(self, prompt: str, session_id: str, agent_id: str, turn_index: int) -> ModelProcess:
        """Start one agent turn in that agent's persistent tmux session."""

        session_name = f"debate-{session_id}-agent-{agent_id}"
        prompt_file = self._write_prompt_file(prompt, session_id, agent_id, turn_index)
        start_marker = self._marker("START", session_id, agent_id, turn_index)
        sentinel = self._marker("DONE", session_id, agent_id, turn_index)
        command = self._build_command(prompt_file, start_marker, sentinel)

        self._ensure_session(session_name)
        self.tmux.send_ctrl_c(session_name)
        time.sleep(0.1)
        self.tmux.clear_history(session_name)
        self.tmux.send_command(session_name, command)

        return ModelProcess(
            session_name=session_name,
            prompt_file=prompt_file,
            start_marker=start_marker,
            sentinel=sentinel,
            command=command,
        )

    def is_running(self, process: ModelProcess) -> bool:
        if not self.tmux.has_session(process.session_name):
            return False
        return self._sentinel_status_match(self.tmux.capture(process.session_name), process) is None

    def capture_output(self, process: ModelProcess) -> str:
        return self.clean_output(self.tmux.capture(process.session_name), process)

    def clean_output(self, output: str, process: ModelProcess) -> str:
        sentinel_match = self._sentinel_status_match(output, process)
        if sentinel_match:
            output = output[: sentinel_match.start()]
        if process.start_marker in output:
            output = output.rsplit(process.start_marker, 1)[1]
        return output.strip()

    def interrupt(self, process: ModelProcess) -> None:
        if not self.tmux.has_session(process.session_name):
            return
        self.tmux.send_ctrl_c(process.session_name)
        time.sleep(0.2)
        self.tmux.send_command(
            process.session_name, self._sentinel_print_command(process.sentinel, 130)
        )

    def stop(self, session_name: str) -> None:
        if not self.tmux.has_session(session_name):
            return
        self.tmux.send_ctrl_c(session_name)
        time.sleep(2)
        self.tmux.kill_session(session_name)

    def _ensure_session(self, session_name: str) -> None:
        if self.tmux.has_session(session_name):
            return
        self.tmux.new_session(session_name, self.cwd)
        self.tmux.set_history_limit(session_name, 50_000)

    def _write_prompt_file(
        self, prompt: str, session_id: str, agent_id: str, turn_index: int
    ) -> Path:
        prompt_dir = self.session_root / f"debate-{session_id}"
        prompt_dir.mkdir(parents=True, exist_ok=True)
        prompt_file = prompt_dir / f"turn_{turn_index:03d}_agent_{agent_id}_prompt.txt"
        prompt_file.write_text(prompt, encoding="utf-8")
        return prompt_file

    def _write_wrapper_script(self, prompt_file: Path) -> Path:
        """Generate an executable zsh wrapper that reads the prompt from a file.

        The wrapper keeps the prompt out of the shell command line and passes it
        to the worker's launch template as a single safely-quoted argument.
        """
        wrapper_file = prompt_file.with_name(prompt_file.stem + "_wrapper.zsh")
        launch_template = self._apply_reasoning(self.launch_template, self.reasoning_level)
        launch_command = launch_template.replace("{{PROMPT}}", "$__debate_prompt")
        script = (
            "#!/usr/bin/env zsh\n"
            f"__debate_prompt=$(< {shlex.quote(str(prompt_file))})\n"
            f"{launch_command}\n"
        )
        wrapper_file.write_text(script, encoding="utf-8")
        wrapper_file.chmod(0o755)
        return wrapper_file

    def _build_command(self, prompt_file: Path, start_marker: str, sentinel: str) -> str:
        if self.prompt_file_launch_template:
            prompt_file_arg = shlex.quote(str(prompt_file))
            launch_template = self._apply_reasoning(
                self.prompt_file_launch_template, self.reasoning_level
            )
            launch_command = launch_template.replace("{{PROMPT_FILE}}", prompt_file_arg)
        else:
            wrapper_file = self._write_wrapper_script(prompt_file)
            launch_command = shlex.quote(str(wrapper_file))
        return (
            f"printf '\\n{start_marker}\\n'; "
            f"{launch_command}; "
            "__debate_status=$?; "
            f"{self._sentinel_print_command(sentinel, '$__debate_status')}"
        )

    def _sentinel_print_command(self, sentinel: str, status: int | str) -> str:
        return f"printf '\\n{sentinel}:%s\\n' {status}"

    def _marker(self, kind: str, session_id: str, agent_id: str, turn_index: int) -> str:
        safe_session_id = re.sub(r"[^A-Za-z0-9_]", "_", session_id)
        safe_agent_id = re.sub(r"[^A-Za-z0-9_]", "_", agent_id)
        return f"__DEBATE_{kind}_{safe_session_id}_{safe_agent_id}_{turn_index}__"

    def _sentinel_status_match(self, output: str, process: ModelProcess) -> re.Match[str] | None:
        matches = list(re.finditer(rf"{re.escape(process.sentinel)}:\d+", output))
        return matches[-1] if matches else None
