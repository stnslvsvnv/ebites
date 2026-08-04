#!/usr/bin/env python3
"""Debate orchestrator - manages conversation state and agent turns."""

from __future__ import annotations

import re
import shutil
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

from .runner import ModelProcess, ModelRunner

ALL_AGENT_IDS = ("A", "B", "C", "D", "E")
AGENT_IDS = ALL_AGENT_IDS


def prune_unsaved_debate_tmp(
    tmp_root: Path | str = Path(".debate/tmp"),
    *,
    is_session_active: Callable[[str], bool] | None = None,
) -> list[Path]:
    """Delete stale debate temp dirs that were never saved."""

    root = Path(tmp_root)
    if not root.exists():
        return []

    active_checker = is_session_active or _has_active_debate_tmux_session
    removed: list[Path] = []
    for session_dir in sorted(root.glob("debate-*")):
        if not session_dir.is_dir():
            continue
        if (session_dir / "transcript.md").exists():
            continue
        if active_checker(session_dir.name):
            continue
        shutil.rmtree(session_dir)
        removed.append(session_dir)
    return removed


def _has_active_debate_tmux_session(session_dir_name: str) -> bool:
    if shutil.which("tmux") is None:
        return False
    for agent_id in AGENT_IDS:
        result = subprocess.run(
            ["tmux", "has-session", "-t", f"{session_dir_name}-agent-{agent_id}"],
            capture_output=True,
        )
        if result.returncode == 0:
            return True
    return False


class DebateOrchestrator:
    """Orchestrates debate between two or three AI agents."""

    CONSENSUS_PATTERN = re.compile(r"^\s*CONSENSUS:", re.IGNORECASE)
    FINAL_ANSWER_PATTERN = re.compile(
        r"<DEBATE_FINAL>\s*(?P<answer>.*?)\s*</DEBATE_FINAL>", re.DOTALL | re.IGNORECASE
    )

    def __init__(
        self,
        model_a_config: dict[str, Any] | None = None,
        model_b_config: dict[str, Any] | None = None,
        initial_prompt: str | None = None,
        max_turns: int | None = None,
        poll_interval_seconds: float = 1.0,
        auto_save: bool = False,
        display_mode: str = "final",
        save_raw_output: bool = True,
        *,
        agent_model_configs: Sequence[dict[str, Any] | None] | None = None,
        model_c_config: dict[str, Any] | None = None,
    ):
        self.agent_runners: dict[str, ModelRunner] = {}
        self._set_agent_configs(
            self._normalize_agent_configs(
                model_a_config,
                model_b_config,
                model_c_config,
                agent_model_configs=agent_model_configs,
            )
        )
        self.max_turns = max_turns
        self.poll_interval_seconds = poll_interval_seconds
        self.auto_save = auto_save
        self.display_mode = display_mode
        self.save_raw_output = save_raw_output
        self.conversation_history: list[dict[str, str]] = []
        self.agent_turn_count = 0
        self.started_turn_count = 0
        self.active_processes: dict[str, ModelProcess] = {}
        self.tmux_sessions: dict[str, str] = {}
        self.tmux_session_a: str | None = None
        self.tmux_session_b: str | None = None
        self.tmux_session_c: str | None = None
        self.tmux_session_d: str | None = None
        self.tmux_session_e: str | None = None
        self.initial_prompt: str | None = None
        self.session_id = ""
        self.session_dir = Path()
        self.transcript_saved = False
        self.reset(initial_prompt=initial_prompt)

    @property
    def model_a(self) -> ModelRunner | None:
        return self.agent_runners.get("A")

    @property
    def model_b(self) -> ModelRunner | None:
        return self.agent_runners.get("B")

    @property
    def model_c(self) -> ModelRunner | None:
        return self.agent_runners.get("C")

    @property
    def model_d(self) -> ModelRunner | None:
        return self.agent_runners.get("D")

    @property
    def model_e(self) -> ModelRunner | None:
        return self.agent_runners.get("E")

    @property
    def active_agent_ids(self) -> list[str]:
        return [agent_id for agent_id in AGENT_IDS if agent_id in self.agent_runners]

    @property
    def first_agent_id(self) -> str:
        return self.active_agent_ids[0]

    @property
    def has_initial_prompt(self) -> bool:
        return bool(self.initial_prompt and self.initial_prompt.strip())

    def reset(self, initial_prompt: str | None = None) -> None:
        """Start a fresh debate session after cleaning up current agent sessions."""

        if self.session_id:
            self.cleanup()
        self.session_id = str(uuid.uuid4())[:8]
        self.session_dir = Path(f".debate/tmp/debate-{self.session_id}")
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.transcript_saved = False
        self.initial_prompt = initial_prompt.strip() if initial_prompt else None
        self.conversation_history.clear()
        self.agent_turn_count = 0
        self.started_turn_count = 0
        self.active_processes.clear()
        self.tmux_sessions.clear()
        self.tmux_session_a = None
        self.tmux_session_b = None
        self.tmux_session_c = None

    def set_initial_prompt(self, prompt: str) -> None:
        cleaned = prompt.strip()
        if not cleaned:
            raise ValueError("Initial prompt cannot be empty")
        if self.conversation_history or self.agent_turn_count:
            raise ValueError("Cannot change initial prompt after debate has started")
        self.initial_prompt = cleaned

    def set_models(
        self,
        model_a_config: dict[str, Any] | None = None,
        model_b_config: dict[str, Any] | None = None,
        model_c_config: dict[str, Any] | None = None,
        model_d_config: dict[str, Any] | None = None,
        model_e_config: dict[str, Any] | None = None,
        *,
        agent_model_configs: Sequence[dict[str, Any] | None] | None = None,
    ) -> None:
        if self.is_any_agent_running():
            raise RuntimeError("Cannot change models while an agent is running")
        configs = self._normalize_agent_configs(
            model_a_config,
            model_b_config,
            model_c_config,
            model_d_config,
            model_e_config,
            agent_model_configs=agent_model_configs,
        )
        self.cleanup()
        self._set_agent_configs(configs)
        self.tmux_sessions.clear()
        self.tmux_session_a = None
        self.tmux_session_b = None
        self.tmux_session_c = None
        self.active_processes.clear()

    def next_agent_id(self, agent_id: str) -> str:
        """Return the next active agent in slot order."""

        active_agents = self.active_agent_ids
        if agent_id not in active_agents:
            raise ValueError(f"Agent {agent_id} is not active")
        index = active_agents.index(agent_id)
        return active_agents[(index + 1) % len(active_agents)]

    def build_agent_prompt(self, agent_id: str, is_first_turn: bool = False) -> str:
        """Build prompt for an agent, including the full debate history."""

        if not self.has_initial_prompt:
            raise ValueError("Initial prompt must be set before starting a debate")

        if agent_id not in self.agent_runners:
            raise ValueError(f"Agent {agent_id} is not active")

        active_labels = ", ".join(f"Agent {active_id}" for active_id in self.active_agent_ids)
        agent_count = len(self.active_agent_ids)
        if agent_count == 2:
            debate_kind = "two-agent"
        elif agent_count == 3:
            debate_kind = "three-agent"
        elif agent_count == 4:
            debate_kind = "four-agent"
        elif agent_count == 5:
            debate_kind = "five-agent"
        else:
            debate_kind = f"{agent_count}-agent"

        system_prompt = f"""You are Agent {agent_id} in a {debate_kind} debate with {active_labels}.

Use the repository instructions and the CLI environment available to you.
Respond to the original task and the conversation so far.

At the end of your turn, print your final debate answer exactly between:
<DEBATE_FINAL>
...
</DEBATE_FINAL>

If you agree and have nothing substantial to add, start the final answer with:
CONSENSUS: <one-line summary of the agreement>

After the CONSENSUS: line, provide a concise structured rationale:
1. Decision: the agreed approach in 1-2 sentences.
2. Key arguments: the strongest reasons supporting it.
3. Residual risks and trade-offs: what could still go wrong or what was sacrificed.
4. Next step: the single most useful action to take next.

Do not use CONSENSUS on your first turn. The first full round is mandatory:
{active_labels} must each provide a substantive answer before consensus can be accepted.
Only on your second or later turn, after every active agent has contributed, may
you start the final answer with CONSENSUS:.

Original task: {self.initial_prompt}
"""

        if is_first_turn and agent_id == self.first_agent_id and not self.conversation_history:
            return system_prompt + "\n\nYou go first. Analyze the task and propose your approach."

        history_lines = ["\n\n--- Conversation History ---"]
        for entry in self.conversation_history:
            role = entry["agent"]
            label = "[USER]:" if role == "USER" else f"[Agent {role}]:"
            history_lines.append(f"\n{label}\n{entry['response']}\n")

        return system_prompt + "".join(history_lines) + f"\n\nYour turn, Agent {agent_id}:"

    def start_turn(self, agent_id: str, is_first_turn: bool = False) -> str:
        """Start a turn for the given agent and return its tmux session name."""

        prompt = self.build_agent_prompt(agent_id, is_first_turn)
        runner = self._runner(agent_id)
        self.started_turn_count += 1
        process = runner.run(prompt, self.session_id, agent_id, self.started_turn_count)
        self.active_processes[agent_id] = process

        self.tmux_sessions[agent_id] = process.session_name
        if agent_id == "A":
            self.tmux_session_a = process.session_name
        elif agent_id == "B":
            self.tmux_session_b = process.session_name
        elif agent_id == "C":
            self.tmux_session_c = process.session_name
        elif agent_id == "D":
            self.tmux_session_d = process.session_name
        elif agent_id == "E":
            self.tmux_session_e = process.session_name

        return process.session_name

    def capture_agent_output(self, agent_id: str) -> str:
        process = self.active_processes.get(agent_id)
        if not process:
            return ""
        return self._runner(agent_id).capture_output(process)

    def is_agent_running(self, agent_id: str) -> bool:
        process = self.active_processes.get(agent_id)
        if not process:
            return False
        return self._runner(agent_id).is_running(process)

    def is_any_agent_running(self) -> bool:
        return any(self.is_agent_running(agent_id) for agent_id in list(self.active_processes))

    def interrupt_agent(self, agent_id: str) -> None:
        process = self.active_processes.get(agent_id)
        if process:
            self._runner(agent_id).interrupt(process)

    def finalize_turn(self, agent_id: str, response: str, interrupted: bool = False) -> str:
        if agent_id not in self.active_agent_ids:
            raise ValueError(f"Agent {agent_id} is not active")
        final_answer = self.extract_final_answer(response)
        if agent_id in self.active_agent_ids and self.save_raw_output:
            self.save_raw_turn(agent_id, response)
        suffix = "\n\n[Turn interrupted by user intervention]" if interrupted else ""
        self.conversation_history.append(
            {
                "agent": agent_id,
                "response": final_answer + suffix,
                "timestamp": datetime.now().isoformat(),
            }
        )
        if agent_id in self.active_agent_ids:
            self.agent_turn_count += 1
        self.active_processes.pop(agent_id, None)
        return final_answer + suffix

    def extract_final_answer(self, response: str) -> str:
        matches = list(self.FINAL_ANSWER_PATTERN.finditer(response))
        if matches:
            return matches[-1].group("answer").strip()
        lines = response.strip().splitlines()
        return "\n".join(lines[-80:]).strip()

    def save_raw_turn(self, agent_id: str, response: str) -> Path:
        raw_dir = self.session_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_file = raw_dir / f"turn_{self.agent_turn_count + 1:03d}_agent_{agent_id}.txt"
        raw_file.write_text(response, encoding="utf-8")
        return raw_file

    def check_consensus(self, response: str) -> bool:
        return bool(self.CONSENSUS_PATTERN.search(response))

    def can_accept_consensus(self, response: str) -> bool:
        """Return whether a consensus marker is valid late enough in the debate."""
        if not self.check_consensus(response):
            return False
        active_agents = set(self.active_agent_ids)
        agent_entries = [
            entry for entry in self.conversation_history if entry["agent"] in active_agents
        ]
        participating_agents = {entry["agent"] for entry in agent_entries}
        return (
            len(agent_entries) >= len(active_agents) + 1 and participating_agents == active_agents
        )

    def should_continue(self) -> bool:
        if not self.has_initial_prompt:
            return False
        if self.conversation_history:
            last_entry = self.conversation_history[-1]
            if last_entry["agent"] in self.active_agent_ids and self.can_accept_consensus(
                last_entry["response"]
            ):
                return False
        if self.max_turns is not None and self.agent_turn_count >= self.max_turns * len(
            self.active_agent_ids
        ):
            return False
        return True

    def add_user_intervention(self, message: str) -> None:
        cleaned = message.strip()
        if not cleaned:
            return
        self.conversation_history.append(
            {"agent": "USER", "response": cleaned, "timestamp": datetime.now().isoformat()}
        )

    def save_transcript(self, filepath: Path | None = None) -> Path:
        if filepath is None:
            filepath = self.session_dir / "transcript.md"
        filepath.parent.mkdir(parents=True, exist_ok=True)

        transcript = f"""# Debate Transcript
Session ID: {self.session_id}
Date: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

## Initial Prompt
{self.initial_prompt or "(not started)"}

## Agents
{self._transcript_agents()}

## Conversation

"""
        for i, entry in enumerate(self.conversation_history, 1):
            agent = entry["agent"]
            response = entry["response"]
            timestamp = entry["timestamp"]

            transcript += f"### Turn {i} - {agent}\n"
            transcript += f"*{timestamp}*\n\n"
            transcript += f"{response}\n\n"
            transcript += "---\n\n"

        filepath.write_text(transcript, encoding="utf-8")
        self.transcript_saved = True
        return filepath

    def cleanup(self) -> None:
        for agent_id, session_name in list(self.tmux_sessions.items()):
            runner = self.agent_runners.get(agent_id)
            if runner:
                runner.stop(session_name)
        self.active_processes.clear()
        self.tmux_sessions.clear()
        self.tmux_session_a = None
        self.tmux_session_b = None
        self.tmux_session_c = None
        self.tmux_session_d = None
        self.tmux_session_e = None
        self._discard_unsaved_session_dir()

    def _discard_unsaved_session_dir(self) -> None:
        if self.transcript_saved or not self.session_dir.exists():
            return
        try:
            self.session_dir.resolve().relative_to(Path(".debate/tmp").resolve())
        except ValueError:
            return
        if not self.session_dir.name.startswith("debate-"):
            return
        shutil.rmtree(self.session_dir)

    def _runner(self, agent_id: str) -> ModelRunner:
        runner = self.agent_runners.get(agent_id)
        if runner is not None:
            return runner
        raise ValueError(f"Unknown agent id: {agent_id}")

    def _normalize_agent_configs(
        self,
        model_a_config: dict[str, Any] | None,
        model_b_config: dict[str, Any] | None,
        model_c_config: dict[str, Any] | None,
        model_d_config: dict[str, Any] | None = None,
        model_e_config: dict[str, Any] | None = None,
        agent_model_configs: Sequence[dict[str, Any] | None] | None = None,
    ) -> tuple[
        dict[str, Any] | None,
        dict[str, Any] | None,
        dict[str, Any] | None,
        dict[str, Any] | None,
        dict[str, Any] | None,
    ]:
        if agent_model_configs is None:
            configs = [
                model_a_config,
                model_b_config,
                model_c_config,
                model_d_config,
                model_e_config,
            ]
        else:
            configs = list(agent_model_configs)
            if len(configs) < 2 or len(configs) > 5:
                raise ValueError("Debate requires between two and five model slots")

        active_count = sum(config is not None for config in configs)
        if active_count < 2:
            raise ValueError("Debate requires at least two active agents")

        while len(configs) < 5:
            configs.append(None)
        return configs[0], configs[1], configs[2], configs[3], configs[4]

    def _set_agent_configs(
        self,
        configs: Sequence[dict[str, Any] | None],
    ) -> None:
        self.agent_runners = {
            agent_id: ModelRunner(config)
            for agent_id, config in zip(ALL_AGENT_IDS, configs)
            if config is not None
        }

    def _transcript_agents(self) -> str:
        return "\n".join(
            f"- Agent {agent_id}: "
            f"{self._runner(agent_id).description} ({self._runner(agent_id).model})"
            for agent_id in self.active_agent_ids
        )
