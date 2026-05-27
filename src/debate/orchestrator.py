#!/usr/bin/env python3
"""Debate orchestrator - manages conversation state and agent turns."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .runner import ModelProcess, ModelRunner


class DebateOrchestrator:
    """Orchestrates debate between two AI agents."""

    CONSENSUS_PATTERN = re.compile(r"^\s*CONSENSUS:", re.IGNORECASE)
    FINAL_ANSWER_PATTERN = re.compile(
        r"<DEBATE_FINAL>\s*(?P<answer>.*?)\s*</DEBATE_FINAL>", re.DOTALL | re.IGNORECASE
    )

    def __init__(
        self,
        model_a_config: dict[str, Any],
        model_b_config: dict[str, Any],
        initial_prompt: str | None = None,
        max_turns: int | None = None,
        poll_interval_seconds: float = 1.0,
        auto_save: bool = False,
        display_mode: str = "final",
        save_raw_output: bool = True,
    ):
        self.model_a = ModelRunner(model_a_config)
        self.model_b = ModelRunner(model_b_config)
        self.max_turns = max_turns
        self.poll_interval_seconds = poll_interval_seconds
        self.auto_save = auto_save
        self.display_mode = display_mode
        self.save_raw_output = save_raw_output
        self.conversation_history: list[dict[str, str]] = []
        self.agent_turn_count = 0
        self.started_turn_count = 0
        self.active_processes: dict[str, ModelProcess] = {}
        self.tmux_session_a: str | None = None
        self.tmux_session_b: str | None = None
        self.initial_prompt: str | None = None
        self.session_id = ""
        self.session_dir = Path()
        self.reset(initial_prompt=initial_prompt)

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
        self.initial_prompt = initial_prompt.strip() if initial_prompt else None
        self.conversation_history.clear()
        self.agent_turn_count = 0
        self.started_turn_count = 0
        self.active_processes.clear()
        self.tmux_session_a = None
        self.tmux_session_b = None

    def set_initial_prompt(self, prompt: str) -> None:
        cleaned = prompt.strip()
        if not cleaned:
            raise ValueError("Initial prompt cannot be empty")
        if self.conversation_history or self.agent_turn_count:
            raise ValueError("Cannot change initial prompt after debate has started")
        self.initial_prompt = cleaned

    def set_models(self, model_a_config: dict[str, Any], model_b_config: dict[str, Any]) -> None:
        if self.is_any_agent_running():
            raise RuntimeError("Cannot change models while an agent is running")
        self.cleanup()
        self.model_a = ModelRunner(model_a_config)
        self.model_b = ModelRunner(model_b_config)
        self.tmux_session_a = None
        self.tmux_session_b = None
        self.active_processes.clear()

    def build_agent_prompt(self, agent_id: str, is_first_turn: bool = False) -> str:
        """Build prompt for an agent, including the full debate history."""

        if not self.has_initial_prompt:
            raise ValueError("Initial prompt must be set before starting a debate")

        system_prompt = f"""You are Agent {agent_id} in a two-agent debate.

Use the repository instructions and the CLI environment available to you.
Respond to the original task and the conversation so far.

At the end of your turn, print your final debate answer exactly between:
<DEBATE_FINAL>
...
</DEBATE_FINAL>

If you agree and have nothing substantial to add, start the final answer with:
CONSENSUS:

Original task: {self.initial_prompt}
"""

        if is_first_turn and agent_id == "A" and not self.conversation_history:
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

        if agent_id == "A":
            self.tmux_session_a = process.session_name
        else:
            self.tmux_session_b = process.session_name

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
        final_answer = self.extract_final_answer(response)
        if agent_id in {"A", "B"} and self.save_raw_output:
            self.save_raw_turn(agent_id, response)
        suffix = "\n\n[Turn interrupted by user intervention]" if interrupted else ""
        self.conversation_history.append(
            {
                "agent": agent_id,
                "response": final_answer + suffix,
                "timestamp": datetime.now().isoformat(),
            }
        )
        if agent_id in {"A", "B"}:
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

    def should_continue(self) -> bool:
        if not self.has_initial_prompt:
            return False
        if self.conversation_history:
            last_agent_response = next(
                (entry["response"] for entry in reversed(self.conversation_history) if entry["agent"] in {"A", "B"}),
                "",
            )
            if last_agent_response and self.check_consensus(last_agent_response):
                return False
        if self.max_turns is not None and self.agent_turn_count >= self.max_turns * 2:
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

        transcript = f"""# Debate Transcript
Session ID: {self.session_id}
Date: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

## Initial Prompt
{self.initial_prompt or "(not started)"}

## Agents
- Agent A: {self.model_a.description} ({self.model_a.model})
- Agent B: {self.model_b.description} ({self.model_b.model})

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
        return filepath

    def cleanup(self) -> None:
        if self.tmux_session_a:
            self.model_a.stop(self.tmux_session_a)
        if self.tmux_session_b and self.tmux_session_b != self.tmux_session_a:
            self.model_b.stop(self.tmux_session_b)
        self.active_processes.clear()

    def _runner(self, agent_id: str) -> ModelRunner:
        if agent_id == "A":
            return self.model_a
        if agent_id == "B":
            return self.model_b
        raise ValueError(f"Unknown agent id: {agent_id}")
