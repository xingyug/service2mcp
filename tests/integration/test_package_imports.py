"""Regression coverage for package import graphs used in deployed containers."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_worker_and_api_submodule_imports_do_not_cycle() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    command = [
        sys.executable,
        "-c",
        (
            "import apps.compiler_worker.celery_app\n"
            "import apps.compiler_api.repository\n"
            "print('ok')\n"
        ),
    ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        cwd=repo_root,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "ok"
