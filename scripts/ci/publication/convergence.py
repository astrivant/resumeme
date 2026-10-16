"""
Project accepted capture measurements, then publish their Matplotlib history with a tag's README.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

from attrs import asdict

from resumeme.config import load_config, project_path
from resumeme.github.convergence import replace_region
from resumeme.linkedin.capture.shards import load_capture_plan, load_capture_shard
from resumeme.visualization.convergence import decode_history, measure, merge_history, render, serialize

_CURRENT = Path(".cache/convergence/current.json")


def collect() -> None:
    """
    Write only approved aggregates from the current tag's complete worker outputs.

    Returns:
        None: The current-run artifact contains no profile, route identifiers or credentials.
    """
    config = load_config(Path("resumeme.config.yaml"))
    if config.readme.convergence_plot is None:
        return
    scope = hashlib.sha256(
        json.dumps(
            {
                "repository": os.environ["GITHUB_REPOSITORY"],
                "owner": config.linkedin.username.casefold(),
                "capture": asdict(config.capture),
                "runner": os.environ["CAPTURE_RUNNER"],
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    sample = measure(
        load_capture_plan(Path(".cache/capture/plan.json")),
        [load_capture_shard(path) for path in sorted(Path(".cache/capture/shards").glob("shard-*.json"))],
        repository=os.environ["GITHUB_REPOSITORY"],
        scope=scope,
        tag=os.environ["GITHUB_REF_NAME"],
        source=os.environ["SOURCE_SHA"],
        run=int(os.environ["GITHUB_RUN_ID"]),
        attempt=int(os.environ["GITHUB_RUN_ATTEMPT"]),
    )
    _CURRENT.parent.mkdir(parents=True, exist_ok=True)
    _CURRENT.write_bytes(serialize([sample]))


def publish() -> None:
    """
    Stage a current-run plot and fork-local history inside the existing main publication lock.

    Returns:
        None: Only configured figure, history and marked README files are staged.

    Raises:
        ValueError: The artifact does not match this repository, source, tag or run.
        subprocess.CalledProcessError: Main history cannot be read or named outputs cannot be staged.
    """
    root = Path.cwd()
    config = load_config(root / "resumeme.config.yaml")
    destination = config.readme.convergence_plot
    paths: list[str] = []
    # Validate all Markdown markers before changing any generated file.
    readmes = {}
    for relative in dict.fromkeys(["README.md", config.readme.output]):
        path = project_path(root, relative)
        if path.exists():
            original = path.read_text(encoding="utf-8")
            updated = replace_region(original, destination, relative)
            if updated != original:
                readmes[path] = updated

    if destination is not None:
        samples = decode_history(_CURRENT.read_bytes())
        if len(samples) != 1:
            raise ValueError("The current capture artifact must contain exactly one observation.")
        current = samples[0]
        if (current.repository, current.source, current.tag, current.run, current.attempt) != (
            os.environ["GITHUB_REPOSITORY"],
            os.environ["SOURCE_SHA"],
            os.environ["GITHUB_REF_NAME"],
            int(os.environ["GITHUB_RUN_ID"]),
            int(os.environ["GITHUB_RUN_ATTEMPT"]),
        ):
            raise ValueError("Convergence artifact does not match this pipeline's repository, source, tag and attempt.")
        image = project_path(root, destination)
        history_path = image.with_suffix(".json")
        relative_history = history_path.relative_to(root).as_posix()
        # Tag commits predate generated publications: read latest main, not the tagged tree's stale history.
        subprocess.run(["git", "fetch", "--no-tags", "origin", "main"], check=True, timeout=60)
        tracked = subprocess.run(
            ["git", "ls-tree", "--name-only", "origin/main", "--", relative_history], capture_output=True, text=True, check=True, timeout=30
        ).stdout.strip()
        history = []
        if tracked:
            data = subprocess.run(["git", "show", f"origin/main:{relative_history}"], capture_output=True, check=True, timeout=30).stdout
            history = decode_history(data)
        samples = merge_history(history, current)
        render(samples, image)
        history_path.write_bytes(serialize(samples))
        paths.extend([destination, relative_history])

    for path, updated in readmes.items():
        path.write_text(updated, encoding="utf-8")
        paths.append(path.relative_to(root).as_posix())
    if paths:
        subprocess.run(["git", "add", "--", *paths], check=True, timeout=30)


def enabled() -> None:
    """
    Resolve the shared publication preference for artifact transfer in CI.

    Returns:
        None: A GitHub output line reports whether this configuration publishes measurements.
    """
    config = load_config(Path("resumeme.config.yaml"))
    print(f"enabled={str(config.readme.convergence_plot is not None).lower()}")


def main() -> None:
    """
    Dispatch capture projection or publication without opening a live browser.

    Returns:
        None: The selected phase finishes or propagates an actionable validation failure.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(required=True)
    commands.add_parser("collect", help="Export current capture aggregates").set_defaults(handler=collect)
    commands.add_parser("publish", help="Render and stage the tag's convergence history").set_defaults(handler=publish)
    commands.add_parser("enabled", help="Report the configured publication preference").set_defaults(handler=enabled)
    args = parser.parse_args()
    args.handler()


if __name__ == "__main__":
    main()
