"""Tests for 5A-compatible headless debate mode."""

from pathlib import Path

from debate.headless import EXIT_AGENT_FAILURE, EXIT_OK, load_prompt_file, run_headless
from debate.orchestrator import DebateOrchestrator
from debate.runner import ModelProcess


class _FakeRunner:
    """Runner that completes each turn instantly with a canned response."""

    description = "fake"
    model = "fake"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def run(self, prompt, session_id, agent_id, turn_index):
        return ModelProcess(
            session_name="fake-session",
            prompt_file=Path(f"/tmp/fake-{turn_index}.txt"),
            start_marker="__START__",
            sentinel="__DONE__",
            command="true",
        )

    def is_running(self, process):
        return False

    def capture_output(self, process):
        response = self.responses[self.calls % len(self.responses)]
        self.calls += 1
        return response

    def interrupt(self, process):
        pass

    def stop(self, session_name):
        pass


def _worker(name, launch='codex exec "{{PROMPT}}"'):
    return {"name": name, "launch": launch, "description": name}


def _orchestrator(responses, max_turns=None):
    orchestrator = DebateOrchestrator(
        model_a_config=_worker("codex"),
        model_b_config=_worker("deepseek"),
        initial_prompt="Task",
        max_turns=max_turns,
        save_raw_output=False,
    )
    orchestrator.agent_runners = {
        "A": _FakeRunner(responses),
        "B": _FakeRunner(responses),
    }
    return orchestrator


def test_headless_loop_reaches_consensus_and_writes_transcript(tmp_path):
    """Headless debate must run until consensus and write the transcript file."""
    out = tmp_path / "transcript.md"
    responses = [
        "<DEBATE_FINAL>\nproposal A\n</DEBATE_FINAL>",
        "<DEBATE_FINAL>\ncritique B\n</DEBATE_FINAL>",
        "CONSENSUS: agreed\n1. Decision: X\n2. Key arguments: Y\n3. Residual risks: Z\n4. Next step: W",
    ]
    status = run_headless(
        _orchestrator(responses),
        prompt="Task",
        transcript_out=out,
        poll_interval_seconds=0.01,
        turn_timeout_seconds=10,
    )
    assert status == EXIT_OK
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "CONSENSUS: agreed" in text


def test_headless_loop_stops_at_max_turns(tmp_path):
    """Headless debate must stop after max_turns without consensus."""
    out = tmp_path / "transcript.md"
    responses = ["<DEBATE_FINAL>\nanswer\n</DEBATE_FINAL>"]
    status = run_headless(
        _orchestrator(responses, max_turns=1),
        prompt="Task",
        transcript_out=out,
        poll_interval_seconds=0.01,
        turn_timeout_seconds=10,
    )
    assert status == EXIT_OK
    assert out.exists()


def test_headless_loop_fails_on_empty_agent_output(tmp_path):
    """Headless debate must return non-zero exit when an agent produces no output."""
    out = tmp_path / "transcript.md"

    class EmptyRunner(_FakeRunner):
        def capture_output(self, process):
            return ""

    orchestrator = _orchestrator(["x"])
    orchestrator.agent_runners = {"A": EmptyRunner(["x"]), "B": EmptyRunner(["x"])}
    status = run_headless(
        orchestrator,
        prompt="Task",
        transcript_out=out,
        poll_interval_seconds=0.01,
        turn_timeout_seconds=1,
    )
    assert status == EXIT_AGENT_FAILURE


def test_headless_respects_prompt_from_file(tmp_path):
    """Headless mode must load the initial prompt from --prompt-file."""
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("Analyze this task deeply.\n", encoding="utf-8")
    assert load_prompt_file(prompt_file) == "Analyze this task deeply."


def test_headless_cli_requires_prompt_and_transcript_flags():
    """CLI must reject headless mode without prompt-file/transcript-out."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "debate.cli", "--headless"],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent.parent),
    )
    assert result.returncode == 3
    assert "requires --prompt-file" in result.stderr
