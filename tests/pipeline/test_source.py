"""
Keep capture routing independent of the commit that happens to run the tests.
"""

from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING

import pytest

from resumeme.tests.paths import REPOSITORY_ROOT

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("author", "marker", "generated"),
    [
        ("Fixture", False, False),
        ("Fixture", True, False),
        ("github-actions[bot]", False, False),
        ("github-actions[bot]", True, True),
    ],
)
@pytest.mark.parametrize(
    ("event", "ref", "requested", "expected"),
    [
        ("schedule", "refs/heads/main", True, True),
        ("workflow_dispatch", "refs/heads/main", True, True),
        ("push", "refs/tags/v0.2.0", False, False),
        ("push", "refs/tags/resume-test", False, False),
        ("push", "refs/tags/resume-test", True, None),
        ("pull_request", "refs/heads/main", True, None),
        ("pull_request", "refs/tags/resume-test", True, None),
        ("push", "refs/heads/main", True, True),
        ("workflow_dispatch", "refs/heads/feature", True, None),
        ("workflow_dispatch", "refs/tags/resume-test", True, None),
        ("schedule", "refs/heads/feature", True, None),
        ("push", "refs/heads/main", False, True),
        ("push", "refs/heads/feature", False, False),
        ("pull_request", "refs/pull/123/merge", False, False),
        ("workflow_dispatch", "refs/heads/main", False, False),
    ],
)
def test_refresh_event_selection(
    tmp_path: Path, author: str, marker: bool, generated: bool, event: str, ref: str, requested: bool, expected: bool | None
) -> None:
    """
    Route explicit source/publication commits through the real pipeline selector.

    Args:
        tmp_path (Path): Isolated Git checkout and output-file directory.
        author (str): Synthetic commit author name.
        marker (bool): Whether the publication trailer is present.
        generated (bool): Whether this author/trailer pair represents a generated publication.
        event (str): Triggering GitHub event.
        ref (str): Event's repository reference.
        requested (bool): Whether the workflow explicitly requested a refresh.
        expected (bool | None): Source-event capture decision, or None for an invalid request.

    Returns:
        None: Only source main pushes automatically capture; all accepted outputs bind the fixture commit.
    """
    # Own every Git input: a bot publication at the real checkout's HEAD must not
    # change these expectations or select the developer's signing key and hooks.
    environment = {
        "PATH": os.environ["PATH"],
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": author,
        "GIT_AUTHOR_EMAIL": "fixture@example.test",
        "GIT_COMMITTER_NAME": "Fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.test",
    }
    message = "Synthetic pipeline source" + ("\n\nResumeme-Publication: true" if marker else "")
    commands = (
        ["git", "init", "--initial-branch=main"],
        ["git", "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", message],
    )

    for command in commands:
        subprocess.run(command, cwd=tmp_path, env=environment, check=True, capture_output=True, text=True)

    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, env=environment, text=True).strip()
    output = tmp_path / "output"
    result = subprocess.run(
        ["bash", str(REPOSITORY_ROOT / "scripts/ci/pipeline/source.sh")],
        cwd=tmp_path,
        env=dict(
            environment,
            REFRESH_PROFILE=str(requested).lower(),
            GITHUB_EVENT_NAME=event,
            GITHUB_REF=ref,
            GITHUB_OUTPUT=str(output),
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is (expected is not None), result.stderr
    assert output.exists() is (expected is not None)

    if expected is not None:
        # A generated main publication suppresses only its automatic push capture;
        # an explicit scheduled/manual refresh still follows the event matrix.
        refresh = False if generated and event == "push" and ref == "refs/heads/main" else expected
        assert output.read_text() == f"sha={sha}\nrefresh={str(refresh).lower()}\n"
