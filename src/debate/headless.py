"""Headless debate driver for non-interactive (5A-style) usage.

Runs the orchestrator turn loop without the TUI, writing the transcript to a
fixed path. Exit contract:

- 0: consensus reached or max_turns exhausted
- 2: an agent produced no usable output (crash/timeout)
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from .orchestrator import DebateOrchestrator

EXIT_OK = 0
EXIT_AGENT_FAILURE = 2


def load_prompt_file(prompt_file: Path) -> str:
    """Read and strip the initial prompt from a file."""

    return Path(prompt_file).read_text(encoding="utf-8").strip()


def run_headless(
    orchestrator: DebateOrchestrator,
    prompt: str,
    transcript_out: Path,
    poll_interval_seconds: float = 1.0,
    turn_timeout_seconds: float = 300.0,
    respond: Callable[[str, str], str] | None = None,
) -> int:
    """Run a debate to consensus or max_turns and write the transcript.

    ``respond`` overrides the runner capture for tests; production callers omit
    it and the real ModelRunner is used.
    """

    orchestrator.set_initial_prompt(prompt)

    try:
        agent_id = orchestrator.first_agent_id
        while orchestrator.should_continue():
            is_first_turn = not orchestrator.conversation_history
            try:
                orchestrator.start_turn(agent_id, is_first_turn=is_first_turn)
            except Exception:
                return EXIT_AGENT_FAILURE

            deadline = time.monotonic() + turn_timeout_seconds
            while orchestrator.is_agent_running(agent_id):
                if time.monotonic() > deadline:
                    orchestrator.interrupt_agent(agent_id)
                    return EXIT_AGENT_FAILURE
                time.sleep(poll_interval_seconds)

            try:
                if respond is not None:
                    process = orchestrator.active_processes.get(agent_id)
                    response = respond(agent_id, str(process.prompt_file) if process else "")
                else:
                    response = orchestrator.capture_agent_output(agent_id)
            except Exception:
                return EXIT_AGENT_FAILURE

            if not response or not response.strip():
                return EXIT_AGENT_FAILURE

            orchestrator.finalize_turn(agent_id, response)
            agent_id = orchestrator.next_agent_id(agent_id)

        orchestrator.save_transcript(Path(transcript_out))
        return EXIT_OK
    finally:
        orchestrator.cleanup()
