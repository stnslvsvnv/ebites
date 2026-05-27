import shutil
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path("src").resolve()))

from debate.runner import ModelRunner


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is required for debate runner")
def test_model_runner_captures_real_tmux_output(tmp_path):
    long_text = "x" * 140
    fake_worker = tmp_path / "fake_worker.zsh"
    fake_worker.write_text(
        "#!/usr/bin/env zsh\n"
        f"printf 'FAKE_WORKER_OUTPUT:%s:{long_text}\\n' \"$1\"\n",
        encoding="utf-8",
    )
    fake_worker.chmod(0o755)

    runner = ModelRunner(
        {
            "name": "fake",
            "launch": f'{fake_worker} "{{{{PROMPT}}}}"',
            "description": "fake worker",
        },
        session_root=tmp_path,
        cwd=tmp_path,
    )
    process = runner.run("hello from debate", session_id="it123", agent_id="A", turn_index=1)

    try:
        deadline = time.monotonic() + 5
        while runner.is_running(process) and time.monotonic() < deadline:
            time.sleep(0.1)

        assert not runner.is_running(process)
        assert runner.capture_output(process) == f"FAKE_WORKER_OUTPUT:hello from debate:{long_text}"
    finally:
        runner.stop(process.session_name)
