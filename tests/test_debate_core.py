import sys
from argparse import Namespace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path("src").resolve()))

from debate.cli import resolve_model_selection
from debate.config import (
    DEFAULT_MODELS_CONFIG_PATH,
    DebateRuntimeConfig,
    load_debate_state,
    resolve_debate_runtime_config,
    resolve_models_config_path,
    save_debate_state,
)
from debate.orchestrator import DebateOrchestrator
from debate.runner import ModelRunner
from debate.tui import DebateApp


def _models_yaml():
    return {
        "workers": {
            "codex": {
                "launch": 'codex exec "{{PROMPT}}"',
                "description": "OpenAI Codex",
            },
            "deepseek-pro": {
                "launch": 'opencode run --model opencode-go/deepseek-v4-pro "{{PROMPT}}"',
                "description": "DeepSeek Pro",
            },
            "glm": {
                "launch": 'opencode run --model opencode-go/glm-5.1 "{{PROMPT}}"',
                "description": "GLM",
            },
            "deepseek-flash": {
                "launch": 'opencode run --model opencode-go/deepseek-v4-flash "{{PROMPT}}"',
                "description": "DeepSeek Flash",
            },
        },
        "profiles": {
            "current": {
                "investigation": "deepseek-pro",
                "implementation": "glm",
                "review": "deepseek-pro",
            },
            "claude": {
                "investigation": "codex",
                "implementation": "codex",
                "review": "codex",
            },
        },
    }


def _args(**overrides):
    values = {
        "models": None,
        "profile": None,
        "preset": None,
        "max_turns": None,
        "models_config": None,
    }
    values.update(overrides)
    return Namespace(**values)


class FakeTmuxClient:
    def __init__(self):
        self.sessions = set()
        self.sent_commands = []
        self.captured_output = ""
        self.killed_sessions = []

    def has_session(self, session_name):
        return session_name in self.sessions

    def new_session(self, session_name, cwd):
        self.sessions.add(session_name)

    def set_history_limit(self, session_name, limit):
        assert session_name in self.sessions

    def send_command(self, session_name, command):
        assert session_name in self.sessions
        self.sent_commands.append(command)

    def send_ctrl_c(self, session_name):
        assert session_name in self.sessions

    def clear_history(self, session_name):
        assert session_name in self.sessions

    def capture(self, session_name):
        assert session_name in self.sessions
        return self.captured_output

    def kill_session(self, session_name):
        self.killed_sessions.append(session_name)
        self.sessions.discard(session_name)


def _worker(name="codex", launch='codex exec "{{PROMPT}}"'):
    return {"name": name, "launch": launch, "description": name}


def test_resolve_model_selection_uses_named_launch_configs():
    models_yaml = _models_yaml()
    runtime_config = DebateRuntimeConfig(models=("deepseek-pro", "codex"))

    model_a, model_b = resolve_model_selection(
        _args(),
        models_yaml,
        runtime_config,
    )

    assert model_a["name"] == "deepseek-pro"
    assert model_a["launch"].startswith("opencode run")
    assert model_b["name"] == "codex"
    assert model_b["launch"] == 'codex exec "{{PROMPT}}"'


def test_resolve_model_selection_trims_custom_models():
    runtime_config = DebateRuntimeConfig(models=("deepseek-pro", "glm"))

    model_a, model_b = resolve_model_selection(
        _args(models=" codex, glm "),
        _models_yaml(),
        runtime_config,
    )

    assert model_a["name"] == "codex"
    assert model_b["name"] == "glm"


def test_debate_runtime_config_reads_default_models():
    config_yaml = {
        "default": {
            "models": ["glm", "deepseek-pro"],
            "max_turns": 7,
            "display_mode": "final",
            "save_raw_output": True,
        },
        "presets": {"fast": {"models": ["deepseek-pro", "glm"], "max_turns": 3}},
    }

    runtime_config = resolve_debate_runtime_config(
        _args(), config_yaml
    )

    assert runtime_config.models == ("glm", "deepseek-pro")
    assert runtime_config.max_turns == 7
    assert runtime_config.display_mode == "final"
    assert runtime_config.save_raw_output is True


def test_debate_runtime_config_supports_presets_and_cli_overrides():
    config_yaml = {
        "default": {"models": ["codex", "deepseek-pro"], "max_turns": None},
        "presets": {"fast": {"models": ["deepseek-flash", "glm"], "max_turns": 4}},
    }

    runtime_config = resolve_debate_runtime_config(
        _args(models=" codex, glm ", preset="fast", max_turns=2), config_yaml
    )

    assert runtime_config.models == ("codex", "glm")
    assert runtime_config.max_turns == 2


def test_debate_runtime_config_uses_last_state_before_default():
    config_yaml = {"default": {"models": ["deepseek-pro", "codex"], "max_turns": None}}
    state_yaml = {"last_models": ["glm", "deepseek-pro"]}

    runtime_config = resolve_debate_runtime_config(_args(), config_yaml, state_yaml)

    assert runtime_config.models == ("glm", "deepseek-pro")


def test_debate_runtime_config_cli_overrides_last_state():
    config_yaml = {"default": {"models": ["deepseek-pro", "codex"]}}
    state_yaml = {"last_models": ["glm", "deepseek-pro"]}

    runtime_config = resolve_debate_runtime_config(
        _args(models="codex,glm"), config_yaml, state_yaml
    )

    assert runtime_config.models == ("codex", "glm")


def test_debate_state_round_trip(tmp_path):
    state_path = tmp_path / "state.yaml"

    save_debate_state(("deepseek-pro", "codex"), state_path)

    assert load_debate_state(state_path) == {"last_models": ["deepseek-pro", "codex"]}


def test_models_config_path_defaults_to_models_yaml():
    assert resolve_models_config_path(_args(), {}) == DEFAULT_MODELS_CONFIG_PATH


def test_models_config_path_reads_debate_config():
    config = {"models_config": "custom/models.yaml"}

    assert resolve_models_config_path(_args(), config) == Path("custom/models.yaml")


def test_models_config_path_cli_override_wins():
    config = {"models_config": "custom/models.yaml"}

    path = resolve_models_config_path(_args(models_config="override/models.yaml"), config)

    assert path == Path("override/models.yaml")


def test_runner_uses_launch_template_and_prompt_file(tmp_path):
    tmux = FakeTmuxClient()
    runner = ModelRunner(_worker(), tmux_client=tmux, session_root=tmp_path)

    process = runner.run("Discuss architecture", session_id="abc123", agent_id="A", turn_index=1)

    assert process.session_name == "debate-abc123-agent-A"
    assert process.prompt_file.read_text() == "Discuss architecture"
    assert tmux.sent_commands
    command = tmux.sent_commands[-1]
    assert "{{PROMPT}}" not in command
    assert 'codex exec "$(cat ' in command
    assert str(process.prompt_file) in command


def test_runner_running_state_and_clean_output_use_sentinel(tmp_path):
    tmux = FakeTmuxClient()
    runner = ModelRunner(_worker(), tmux_client=tmux, session_root=tmp_path)
    process = runner.run("Prompt", session_id="abc123", agent_id="A", turn_index=1)

    tmux.captured_output = f"typed command\n{process.start_marker}\npartial output"
    assert runner.is_running(process)
    assert runner.clean_output(tmux.captured_output, process) == "partial output"

    tmux.captured_output = (
        f"typed command\n{process.start_marker}\nfinal output\n{process.sentinel}:0\nshell prompt"
    )
    assert not runner.is_running(process)
    assert runner.clean_output(tmux.captured_output, process) == "final output"


def test_orchestrator_waits_for_initial_prompt_and_builds_context():
    orchestrator = DebateOrchestrator(
        model_a_config=_worker("codex"), model_b_config=_worker("deepseek"), max_turns=1
    )

    assert not orchestrator.has_initial_prompt

    orchestrator.set_initial_prompt("Read the repository notes and discuss feature X")
    orchestrator.finalize_turn("A", "Proposal from A")
    orchestrator.add_user_intervention("Check performance impact")

    prompt = orchestrator.build_agent_prompt("B")

    assert "Original task: Read the repository notes and discuss feature X" in prompt
    assert "[Agent A]:" in prompt
    assert "Proposal from A" in prompt
    assert "[USER]:" in prompt
    assert "Check performance impact" in prompt


def test_orchestrator_prompt_is_minimal_and_requires_final_markers():
    orchestrator = DebateOrchestrator(
        model_a_config=_worker("codex"), model_b_config=_worker("deepseek"), initial_prompt="Task"
    )

    prompt = orchestrator.build_agent_prompt("A", is_first_turn=True)

    assert "<DEBATE_FINAL>" in prompt
    assert "</DEBATE_FINAL>" in prompt
    assert "Use these exact tools" not in prompt


def test_second_agent_prompt_includes_initial_prompt_and_first_final_answer():
    orchestrator = DebateOrchestrator(
        model_a_config=_worker("deepseek-pro"),
        model_b_config=_worker("codex"),
        initial_prompt="Read a tiny file and comment",
    )
    orchestrator.finalize_turn("A", "<DEBATE_FINAL>First final answer</DEBATE_FINAL>")

    prompt = orchestrator.build_agent_prompt("B")

    assert "Original task: Read a tiny file and comment" in prompt
    assert "First final answer" in prompt


def test_orchestrator_stores_only_final_answer_and_saves_raw_output(tmp_path):
    orchestrator = DebateOrchestrator(
        model_a_config=_worker("codex"),
        model_b_config=_worker("deepseek"),
        initial_prompt="Task",
        save_raw_output=True,
    )
    orchestrator.session_dir = tmp_path
    raw_output = "tool noise\n<DEBATE_FINAL>\nFinal answer only\n</DEBATE_FINAL>\nmore noise"

    orchestrator.finalize_turn("A", raw_output)

    assert orchestrator.conversation_history[-1]["response"] == "Final answer only"
    raw_file = tmp_path / "raw" / "turn_001_agent_A.txt"
    assert raw_file.read_text(encoding="utf-8") == raw_output


def test_save_transcript_includes_initial_prompt_and_full_history(tmp_path):
    orchestrator = DebateOrchestrator(
        model_a_config=_worker("deepseek-pro"),
        model_b_config=_worker("codex"),
        initial_prompt="Original task",
    )
    orchestrator.session_dir = tmp_path
    orchestrator.finalize_turn("A", "<DEBATE_FINAL>Agent A answer</DEBATE_FINAL>")
    orchestrator.finalize_turn("B", "<DEBATE_FINAL>Agent B answer</DEBATE_FINAL>")
    orchestrator.add_user_intervention("User clarification")

    transcript_path = orchestrator.save_transcript()
    transcript = transcript_path.read_text(encoding="utf-8")

    assert "## Initial Prompt" in transcript
    assert "Original task" in transcript
    assert "Agent A answer" in transcript
    assert "Agent B answer" in transcript
    assert "User clarification" in transcript


def test_orchestrator_final_answer_fallback_uses_tail_without_marker():
    orchestrator = DebateOrchestrator(
        model_a_config=_worker("codex"), model_b_config=_worker("deepseek"), initial_prompt="Task"
    )
    raw_output = "line 1\nline 2\nline 3\nline 4"

    assert orchestrator.extract_final_answer(raw_output) == "line 1\nline 2\nline 3\nline 4"


def test_orchestrator_max_turns_counts_full_rounds():
    orchestrator = DebateOrchestrator(
        model_a_config=_worker("codex"),
        model_b_config=_worker("deepseek"),
        initial_prompt="Task",
        max_turns=1,
    )

    assert orchestrator.should_continue()
    orchestrator.finalize_turn("A", "A response")
    assert orchestrator.should_continue()
    orchestrator.finalize_turn("B", "B response")
    assert not orchestrator.should_continue()


def test_consensus_requires_response_prefix():
    orchestrator = DebateOrchestrator(
        model_a_config=_worker("codex"), model_b_config=_worker("deepseek"), initial_prompt="Task"
    )

    assert orchestrator.check_consensus("CONSENSUS: ship it")
    assert orchestrator.check_consensus("\n  CONSENSUS: ship it")
    assert not orchestrator.check_consensus("The prompt mentioned CONSENSUS: but no agreement")


@pytest.mark.asyncio
async def test_tui_waits_for_initial_prompt_before_starting_debate():
    app = DebateApp(
        DebateOrchestrator(
            model_a_config=_worker("codex"),
            model_b_config=_worker("deepseek"),
        )
    )

    async with app.run_test():
        assert app.debate_task is None
        assert not app.orchestrator.has_initial_prompt


@pytest.mark.asyncio
async def test_tui_does_not_mount_ascii_logo():
    app = DebateApp(
        DebateOrchestrator(
            model_a_config=_worker("codex"),
            model_b_config=_worker("deepseek"),
        )
    )

    async with app.run_test():
        assert len(app.query("#logo")) == 0


def test_tui_uses_textual_default_theme_tokens():
    css = DebateApp.CSS

    assert "$surface" in css
    assert "$primary" in css
    assert "$accent" in css
    assert "theme-night" not in css
    assert "#f2eadc" not in css
    assert "#20150f" not in css


def test_tui_status_label_uses_worker_names():
    app = DebateApp(
        DebateOrchestrator(
            model_a_config=_worker("deepseek-pro"),
            model_b_config=_worker("codex"),
        )
    )

    assert "Agent A: deepseek-pro" in app.status_text("Enter initial prompt")
    assert "Agent B: codex" in app.status_text("Enter initial prompt")


@pytest.mark.asyncio
async def test_tui_models_menu_selects_two_numbered_models(tmp_path):
    app = DebateApp(
        DebateOrchestrator(
            model_a_config=_worker("deepseek-pro"),
            model_b_config=_worker("codex"),
        ),
        models_yaml=_models_yaml(),
        state_path=tmp_path / "state.yaml",
    )

    async with app.run_test():
        await app.handle_user_message("/models")
        assert app.model_selection_active
        await app.handle_user_message("3 1")

    assert app.orchestrator.model_a.model == "glm"
    assert app.orchestrator.model_b.model == "codex"
    assert load_debate_state(tmp_path / "state.yaml") == {"last_models": ["glm", "codex"]}


@pytest.mark.asyncio
async def test_next_prompt_after_finished_starts_new_debate(monkeypatch):
    app = DebateApp(
        DebateOrchestrator(
            model_a_config=_worker("deepseek-pro"),
            model_b_config=_worker("codex"),
        )
    )
    starts = []

    async def fake_run_debate():
        starts.append(app.orchestrator.initial_prompt)
        app.debate_finished = True

    monkeypatch.setattr(app, "run_debate", fake_run_debate)

    async with app.run_test():
        await app.handle_user_message("first prompt")
        await app.debate_task
        await app.handle_user_message("second prompt")
        await app.debate_task

    assert starts == ["first prompt", "second prompt"]


@pytest.mark.asyncio
async def test_tui_cleans_up_agent_sessions_after_consensus(monkeypatch):
    orchestrator = DebateOrchestrator(
        model_a_config=_worker("deepseek-pro"),
        model_b_config=_worker("codex"),
        initial_prompt="Task",
    )
    cleanup_calls = []

    monkeypatch.setattr(orchestrator, "start_turn", lambda *args, **kwargs: "session")
    monkeypatch.setattr(orchestrator, "is_agent_running", lambda agent_id: False)
    monkeypatch.setattr(
        orchestrator,
        "capture_agent_output",
        lambda agent_id: "<DEBATE_FINAL>CONSENSUS: done</DEBATE_FINAL>",
    )
    monkeypatch.setattr(orchestrator, "cleanup", lambda: cleanup_calls.append(True))

    app = DebateApp(orchestrator)

    async with app.run_test():
        await app.run_debate()

    assert cleanup_calls == [True]
