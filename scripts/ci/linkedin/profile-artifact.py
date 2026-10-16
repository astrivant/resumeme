"""
Transfer only the captured profile and its referenced media between verified CI stages.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from resumeme.compiler.asts.profile import load_profile, save_profile
from resumeme.compiler.passes.privacy import without_profile_location
from resumeme.config import load_config, project_path

if TYPE_CHECKING:
    from collections.abc import Iterator

    from resumeme.compiler.asts.profile import Entry, Media


def entry_images(entries: list[Entry]) -> Iterator[Media]:
    """
    Include nested role previews when collecting the snapshot's owned media.

    Args:
        entries (list[Entry]): Captured entries with optional nested positions.

    Yields:
        Media: Every referenced image, including children of company groups.
    """
    for entry in entries:
        yield from entry.images
        yield from entry_images(entry.positions)


def transfer(mode: str, root: Path) -> list[Path]:
    """
    Export, restore, or stage a complete capture using configured paths and an explicit file allowlist.

    Args:
        mode (str): Export to job storage, restore from it, or stage restored inputs in Git.
        root (Path): Checkout containing resumeme.config.yaml.

    Returns:
        list[Path]: Validated local input paths; browser sessions and diagnostics remain local.

    Raises:
        ValueError: Capture is incomplete, a media reference escapes the configured assets directory, or a file is missing.
    """
    config = load_config(root / "resumeme.config.yaml")
    artifact = root / ".cache/refresh"
    snapshot = project_path(root, config.output.profile)
    assets = project_path(root, config.output.assets)
    profile_path = artifact / "profile.json" if mode == "restore" else snapshot
    profile = load_profile(profile_path, config.linkedin.username)

    if profile.warnings:
        raise ValueError("A profile refresh cannot publish an incomplete profile: " + "; ".join(profile.warnings))

    # Redact personal location before determining which images and fields enter a published artifact.
    redact_location = not config.style.display_location

    if redact_location:
        profile = without_profile_location(profile)

    # The profile owns the file list; incidental assets, Firefox state, and diagnostic pages are never transferred.
    pairs = {(snapshot, artifact / "profile.json")}
    images = [*profile.images, *(image for section in profile.sections for image in entry_images(section.entries))]

    for image in images:
        if not image.path:
            raise ValueError("A captured image has no downloaded file.")

        path = project_path(root, image.path)

        if not path.is_relative_to(assets):
            raise ValueError("Captured media must reside under output.assets.")

        pairs.add((path, project_path(artifact, str(Path("assets") / path.relative_to(assets)))))

    # Validate the complete transfer before copying anything, so a missing media file cannot produce a partial publication.
    for local, stored in pairs:
        source = stored if mode == "restore" else local

        if not source.is_file() or source.is_symlink():
            raise ValueError(f"Missing captured input: {source}")

    # Persist the validated, sanitized profile only after every referenced file passes the transfer checks.
    if redact_location and mode != "inspect":
        save_profile(profile, profile_path)

    paths = sorted(local for local, _ in pairs)
    if mode == "inspect":
        return paths

    if mode == "stage":
        subprocess.run(["git", "add", "--", *(str(local.relative_to(root)) for local, _ in sorted(pairs))], cwd=root, check=True)
        return paths

    for local, stored in sorted(pairs):
        source, destination = (stored, local) if mode == "restore" else (local, stored)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    return paths


def receipt(mode: str, root: Path) -> None:
    """
    Bind accepted profile/media bytes to a verified main capture before tagging.

    Args:
        mode (str): Accept in the gated main publisher, or verify a committed snapshot for release.
        root (Path): Repository containing the configured capture.

    Returns:
        None: Acceptance stages its receipt; verification rejects unpublished or changed data.

    Raises:
        ValueError: Inputs, provenance, repository or publication ancestry do not match.
        subprocess.CalledProcessError: Git cannot establish the committed acceptance boundary.
    """
    config = load_config(root / "resumeme.config.yaml")
    target = project_path(root, config.output.profile).with_suffix(".capture.json")
    paths = transfer("stage" if mode == "accept" else "inspect", root)
    files = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    if target in paths:
        raise ValueError("Capture receipt collides with a captured input.")
    if mode == "accept":
        if os.environ.get("GITHUB_REF") != "refs/heads/main":
            raise ValueError("Only the verified main publisher can accept a capture.")
        value = {
            "version": 1,
            "repository": os.environ["GITHUB_REPOSITORY"],
            "ref": "refs/heads/main",
            "source": os.environ["SOURCE_SHA"],
            "run": int(os.environ["GITHUB_RUN_ID"]),
            "attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
            "files": files,
        }
        target.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        subprocess.run(["git", "add", "--", target.relative_to(root).as_posix()], cwd=root, check=True)
        return

    if not target.is_file() or target.is_symlink():
        raise ValueError("No accepted capture receipt. Complete main capture/publication, then tag its accepted commit.")
    value = json.loads(target.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "repository", "ref", "source", "run", "attempt", "files"}
        or value["version"] != 1
        or value["repository"] != os.environ["GITHUB_REPOSITORY"]
        or value["ref"] != "refs/heads/main"
        or not isinstance(value["source"], str)
        or not re.fullmatch(r"[a-f0-9]{40}", value["source"])
        or type(value["run"]) is not int
        or value["run"] < 1
        or type(value["attempt"]) is not int
        or value["attempt"] < 1
        or value["files"] != files
    ):
        raise ValueError("Capture inputs or provenance differ from accepted main data; publish a new main capture before tagging.")

    # Verify committed bytes, not a locally generated receipt or unstaged replacement.
    for path in [*paths, target]:
        committed = subprocess.run(
            ["git", "show", f"HEAD:{path.relative_to(root).as_posix()}"], cwd=root, check=True, capture_output=True
        ).stdout
        if committed != path.read_bytes():
            raise ValueError("Release capture inputs and receipt must be committed.")
    accepted = subprocess.run(
        ["git", "log", "-1", "--format=%H", "--", target.relative_to(root).as_posix()],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    parent = subprocess.run(["git", "rev-parse", f"{accepted}^"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    marker = subprocess.run(
        ["git", "show", "-s", "--format=%(trailers:key=Resumeme-Publication,valueonly)", accepted],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if parent != value["source"] or marker != "true":
        raise ValueError("Receipt must originate from main publication immediately after its captured source.")
    subprocess.run(["git", "merge-base", "--is-ancestor", accepted, "origin/main"], cwd=root, check=True)
    subprocess.run(["git", "merge-base", "--is-ancestor", "HEAD", "origin/main"], cwd=root, check=True)


def main() -> None:
    """
    Select the transfer operation used by the calling CI stage.

    Returns:
        None: Requested profile transfer is complete.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("export", "restore", "stage", "accept", "verify"))
    args = parser.parse_args()
    if args.mode in {"accept", "verify"}:
        receipt(args.mode, Path.cwd())
    else:
        transfer(args.mode, Path.cwd())


if __name__ == "__main__":
    main()
