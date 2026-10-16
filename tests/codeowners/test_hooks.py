"""
Exercise installed hook behavior for ownership edits and path-only changes.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from tests.codeowners.test_compiler import git

if TYPE_CHECKING:
    import pytest


ROOT = Path(__file__).resolve().parents[2]


def test_hooks_normalize_without_staging_and_check_committed_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Run the configured commit and push hooks against independent Git fixture history.

    Args:
        tmp_path (Path): Temporary worktree and pre-commit cache.
        monkeypatch (pytest.MonkeyPatch): Isolate fixture Git configuration and module lookup.

    Returns:
        None: Formatting stops the commit, committed drift blocks push, and path additions trigger normalization.
    """
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("PRE_COMMIT_HOME", str(tmp_path / ".cache/pre-commit"))
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "Fixture")
    git(tmp_path, "config", "user.email", "fixture@example.invalid")
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())
    assert set(config["default_install_hook_types"]) == {"pre-commit", "pre-push"}
    hooks = [
        hook for repo in config["repos"] if repo["repo"] == "local" for hook in repo["hooks"] if hook["id"].startswith("codeowners-tree")
    ]
    assert len(hooks) == 2
    # Keep the real selectors and modes; only substitute a fixture-safe interpreter and absolute import root.
    for hook in hooks:
        arguments = shlex.split(hook["entry"])
        arguments = arguments[arguments.index("-m") :]
        hook["entry"] = shlex.join([sys.executable, *arguments])
    (tmp_path / ".pre-commit-config.yaml").write_text(yaml.safe_dump({"repos": [{"repo": "local", "hooks": hooks}]}))
    (tmp_path / ".github").mkdir()
    ownership = tmp_path / ".github/CODEOWNERS"
    ownership.write_text("/z @one\n/a @two\n")
    for name in ("a", "z"):
        (tmp_path / name).write_text(name)
    git(tmp_path, "add", ".pre-commit-config.yaml", ".github/CODEOWNERS", "a", "z")
    git(tmp_path, "commit", "-m", "Synthetic ownership baseline")

    def hook_run(identifier: str, stage: str, expected: int) -> None:
        """
        Invoke pre-commit on a non-CODEOWNERS path to exercise whole-map selection.

        Args:
            identifier (str): Configured ownership hook ID.
            stage (str): Commit or push lifecycle stage.
            expected (int): Expected process status.

        Returns:
            None: The actual hook invocation matches its expected exit status.
        """
        result = subprocess.run(
            [sys.executable, "-m", "pre_commit", "run", identifier, "--hook-stage", stage, "--files", "a"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert result.returncode == expected, result.stdout + result.stderr
        assert "Skipped" not in result.stdout

    hook_run("codeowners-tree", "pre-commit", 1)
    assert ownership.read_text() == "/a @two\n/z @one\n"
    assert not git(tmp_path, "diff", "--cached")
    hook_run("codeowners-tree", "pre-commit", 0)
    hook_run("codeowners-tree-push", "pre-push", 1)
    git(tmp_path, "add", ".github/CODEOWNERS")
    git(tmp_path, "commit", "-m", "Normalize ownership")
    hook_run("codeowners-tree-push", "pre-push", 0)

    # An absent path is a preserved boundary until a newly tracked file makes the rule transformable.
    ownership.write_text("/z @one\n/future @one\n/a @two\n")
    git(tmp_path, "add", ".github/CODEOWNERS")
    git(tmp_path, "commit", "-m", "Reserve a future path")
    hook_run("codeowners-tree", "pre-commit", 0)
    (tmp_path / "future").write_text("new path")
    git(tmp_path, "add", "future")
    hook_run("codeowners-tree", "pre-commit", 1)
    assert ownership.read_text() == "/a @two\n/future @one\n/z @one\n"
    assert git(tmp_path, "diff", "--cached", "--name-only").strip() == "future"
