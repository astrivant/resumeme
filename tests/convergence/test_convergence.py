"""
Exercise public convergence evidence, fork isolation and real Git publication handoff.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from attrs import evolve
from jsonschema import ValidationError
from matplotlib.figure import Figure
from scripts.ci.publication.convergence import collect, publish

from resumeme.compiler.asts.profile import Profile, Section
from resumeme.config import Config, LinkedIn, Readme, load_config
from resumeme.exceptions import ConfigurationError, PublicationError
from resumeme.github.convergence import markup, replace_region
from resumeme.github.readme import render_readme
from resumeme.linkedin.capture.feedback import TimingFeedback
from resumeme.linkedin.capture.shards import CaptureRoute, CaptureShard, assign_routes, make_capture_plan
from resumeme.visualization.convergence import Observation, decode_history, measure, merge_history, render, serialize

if TYPE_CHECKING:
    from pytest import MonkeyPatch


def sample() -> Observation:
    """
    Provide an invented aggregate without exposing real capture records.

    Returns:
        Observation: Synthetic warm capture for publication tests.
    """
    return Observation("example/resume", "a" * 64, "b" * 40, 100, 1, 2, 2, 2, 10.0, 20.0, 60.0)


def test_measurement_uses_frozen_predictions_and_does_not_disclose_routes() -> None:
    """
    Keep raw residuals visible even when the PID deadband would suppress them.

    Returns:
        None: Cold starts remain unknown and one partly calibrated run uses only known route durations.
    """
    routes = [CaptureRoute(key, key, f"https://www.linkedin.com/in/fixture/details/{key}/", "detail") for key in ["secret-a", "secret-b"]]
    plan = make_capture_plan(Profile("fixture", "Private name"), "firefox", routes, shard_count=2)
    shards = [
        CaptureShard(
            plan.capture_id,
            "firefox",
            index,
            2,
            [Section(route.key, route.title) for route in assigned],
            {route.key: 10.0 for route in assigned},
        )
        for index, assigned in assign_routes(plan).items()
    ]
    metadata = {"repository": "example/resume", "scope": "a" * 64, "source": "b" * 40}
    cold = measure(plan, shards, **metadata, run=100, attempt=1)
    assert cold.error_percent is None
    assert cold.calibrated == 0
    warm = evolve(plan, routes=[evolve(routes[0], estimated_seconds=9.9, feedback=TimingFeedback(9.9, 0, 0, 1)), routes[1]])
    # Freeze the original assignments when testing estimates independently of scheduler placement.
    warm = evolve(warm, placements={route.unit_key: index for index, assigned in assign_routes(plan).items() for route in assigned})
    observed = measure(warm, shards, **metadata, run=100, attempt=1)
    assert observed.error_percent == pytest.approx(1.0)
    assert observed.calibrated == 1
    assert observed.dispersion_percent == 0
    encoded = serialize([observed])
    assert all(secret not in encoded for secret in [b"secret-a", b"Private name", b"linkedin.com", b"fixture", b"integral"])
    with pytest.raises(ValueError):
        measure(warm, shards[:1], **metadata, run=100, attempt=1)


def test_history_reruns_scopes_and_bounds() -> None:
    """
    Isolate forks and configuration changes while preserving one observation per main run.

    Returns:
        None: Reruns replace points, stale attempts are ignored, and histories remain bounded.
    """
    original = sample()
    retry = evolve(original, attempt=2, peak_seconds=55)
    assert merge_history([original], retry) == [retry]
    assert merge_history([retry], original) == [retry]
    fork = evolve(original, repository="another/fork", run=101)
    assert merge_history([original], fork) == [fork]
    changed = evolve(original, scope="c" * 64)
    assert merge_history([original], changed) == [changed]
    with pytest.raises(ValueError, match="changed run source"):
        merge_history([original], evolve(original, source="d" * 40))
    history = [evolve(original, run=index) for index in range(1, 61)]
    result = merge_history(history, evolve(original, run=61))
    assert len(result) == 60
    assert result[0].run == 2
    assert decode_history(serialize(result)) == result
    with pytest.raises(ValueError):
        decode_history(serialize([evolve(original, peak_seconds=-1)]))
    with pytest.raises(ValueError):
        decode_history(b'{"version":1,"samples":[{"profile":"must not be accepted"}]}')


def test_html_regions_are_fork_relative_and_preserve_authored_content() -> None:
    """
    Support nested Markdown and configured destinations without hardcoded upstream URLs.

    Returns:
        None: Only the marked block changes and malformed or duplicate markers fail visibly.
    """
    original = "before\n<!-- resumeme:convergence:start -->\nold\n<!-- resumeme:convergence:end -->\nafter\n"
    updated = replace_region(original, "plots/capture.svg", "docs/profile/README.md")
    assert 'src="../../plots/capture.svg"' in updated
    assert 'href="../../plots/capture.json"' in updated
    assert updated.startswith("before\n") and updated.endswith("\nafter\n")
    assert replace_region(updated, "plots/capture.svg", "docs/profile/README.md") == updated
    assert replace_region("custom page", "plots/capture.svg", "README.md") == "custom page"
    assert "<img" not in markup(None, "README.md")
    with pytest.raises(PublicationError):
        replace_region(original + original, "plots/capture.svg", "README.md")
    with pytest.raises(PublicationError):
        replace_region("<!-- resumeme:convergence:end --><!-- resumeme:convergence:start -->", None, "README.md")


@pytest.mark.parametrize("destination", ["../escape.svg", "/absolute.svg", ".git/config.svg", "image.png", "data/profile.svg"])
def test_config_rejects_escaping_or_colliding_destinations(tmp_path: Path, destination: str) -> None:
    """
    Fail path errors before collection or publication can overwrite profile inputs.

    Args:
        tmp_path (Path): Isolated configuration root.
        destination (str): Invalid or colliding SVG destination.

    Returns:
        None: Configuration rejects every unsupported destination.
    """
    path = tmp_path / "resumeme.config.yaml"
    path.write_text("linkedin: {username: fixture}\npublishing:\n  readme:\n    convergence_plot: " + destination + "\n")
    with pytest.raises((ConfigurationError, ValidationError)):
        load_config(path)


def test_matplotlib_is_reproducible_and_shows_empty_and_cold_states(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """
    Render all meaningful input cardinalities without inventing a measured zero.

    Args:
        tmp_path (Path): Temporary figure destinations.
        monkeypatch (MonkeyPatch): Inspects axis bounds at the actual save boundary.

    Returns:
        None: Real SVG output is deterministic and identifies missing calibration.
    """
    limits: list[float] = []
    original_save = Figure.savefig

    def save(figure: Figure, output: Path, *, metadata: dict[str, str | None]) -> None:
        """
        Capture visible upper bounds before writing the real figure.

        Args:
            figure (Figure): Fully populated figure.
            output (Path): Requested artifact path.
            metadata (dict[str, str | None]): Stable SVG metadata.

        Returns:
            None: The real renderer writes bytes while the test retains axis bounds.
        """
        limits[:] = [float(axis.get_ylim()[1]) for axis in figure.axes]
        original_save(figure, output, metadata=metadata)

    monkeypatch.setattr(Figure, "savefig", save)
    path = tmp_path / "plot.svg"
    render([], path)
    assert "Awaiting first main capture" in path.read_text()
    cold = evolve(sample(), calibrated=0, error_percent=None)
    render([cold], path)
    assert "Cold start: no prior PID estimates" in path.read_text()
    render([cold, evolve(sample(), run=101)], path)
    first = path.read_bytes()
    render([cold, evolve(sample(), run=101)], path)
    assert path.read_bytes() == first
    assert limits[0] >= 10.0
    assert limits[1] >= sample().dispersion_percent
    assert limits[2] >= sample().peak_seconds


def git(root: Path, *args: str) -> str:
    """
    Run isolated fixture Git commands with no production signing identity or remote.

    Args:
        root (Path): Temporary test repository.
        *args (str): Explicit Git arguments.

    Returns:
        str: Captured standard output.
    """
    return subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_publication_reads_main_history_and_checks_artifact_binding(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """
    Exercise latest-main history and explicit staging with a real local Git origin.

    Args:
        tmp_path (Path): Isolated origin and main source checkout.
        monkeypatch (MonkeyPatch): Supplies synthetic Actions metadata.

    Returns:
        None: History missing from the old source is retained, and mismatched artifacts cannot be staged.
    """
    upstream, checkout = tmp_path / "upstream", tmp_path / "checkout"
    upstream.mkdir()
    git(upstream, "init", "-b", "main")
    (upstream / "resumeme.config.yaml").write_text(
        "linkedin: {username: fixture}\npublishing: {readme: {convergence_plot: plots/live.svg}}\n"
    )
    (upstream / "README.md").write_text("intro\n" + markup("plots/live.svg", "README.md") + "\nend\n")
    git(upstream, "add", "resumeme.config.yaml", "README.md")
    git(upstream, "commit", "-m", "source")
    source = git(upstream, "rev-parse", "HEAD")
    (upstream / "plots").mkdir()
    previous = evolve(sample(), source=source)
    (upstream / "plots/live.json").write_bytes(serialize([previous]))
    git(upstream, "add", "plots/live.json")
    git(upstream, "commit", "-m", "previous generated evidence")
    git(tmp_path, "clone", str(upstream), str(checkout))
    git(checkout, "checkout", "--detach", source)
    monkeypatch.chdir(checkout)
    current = evolve(previous, run=101)
    artifact = checkout / ".cache/convergence/current.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(serialize([current]))
    for name, value in {
        "GITHUB_REPOSITORY": current.repository,
        "SOURCE_SHA": source,
        "GITHUB_REF_NAME": "v2",
        "GITHUB_RUN_ID": "101",
        "GITHUB_RUN_ATTEMPT": "1",
    }.items():
        monkeypatch.setenv(name, value)
    publish()
    assert decode_history((checkout / "plots/live.json").read_bytes()) == [previous, current]
    assert set(git(checkout, "diff", "--cached", "--name-only").splitlines()) == {"plots/live.svg", "plots/live.json"}
    assert (checkout / "README.md").read_text().endswith("\nend\n")
    artifact.write_bytes(serialize([evolve(current, repository="wrong/fork")]))
    with pytest.raises(ValueError, match="does not match"):
        publish()


def test_ci_owns_complete_capture_artifact_and_main_publication() -> None:
    """
    Keep the actual producer and consumer in the existing verified main pipeline.

    Returns:
        None: Collection follows validation, publication follows verification, and no raw capture artifact is uploaded for plotting.
    """
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/ci.yml").read_text()
    stage = (root / ".github/workflows/stage-deploy.yml").read_text()
    assert workflow.index("Check the accepted profile") < workflow.index("convergence.py collect")
    assert "path: .cache/convergence/current.json" in workflow
    assert "needs: [source, release-stage]" in workflow
    publisher = (root / "scripts/ci/publication/publish.sh").read_text()
    assert publisher.index("convergence.py publish") < publisher.index("git commit")
    assert "needs: [source, verified]" in workflow
    assert "group: resumeme-pdf-main" in stage
    assert "if: inputs.refresh && steps.convergence.outputs.enabled == 'true'" in stage


def test_disabled_publication_needs_no_capture_and_personal_page_uses_its_destination(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """
    Honor disabled publication before requiring artifacts and render nested fork markup from configuration.

    Args:
        tmp_path (Path): Empty checkout with publication disabled.
        monkeypatch (MonkeyPatch): Selects the isolated configuration root.

    Returns:
        None: No metrics artifact is created; generated fork pages use configured relative destinations.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "resumeme.config.yaml").write_text("linkedin: {username: fixture}\npublishing: {readme: {convergence_plot: null}}\n")
    collect()
    assert not (tmp_path / ".cache/convergence/current.json").exists()
    config = Config(LinkedIn("fixture"), readme=Readme(output="docs/profile/README.md", convergence_plot="charts/live.svg"))
    markdown = render_readme(Profile("fixture", "Example"), config, "another/fork", 1)
    assert 'src="../../charts/live.svg"' in markdown
    assert 'href="../../charts/live.json"' in markdown
    assert 'src="https://raw.githubusercontent.com/astrivant/' not in markdown
