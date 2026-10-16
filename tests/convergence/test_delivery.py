"""
Verify main acceptance and tag completion boundaries with local Git and synthetic data.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from resumeme.compiler.asts.profile import Media, Profile, save_profile

from .test_convergence import git

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("invalid", [None, "missing", "profile", "media", "repository", "uncommitted", "ancestry"])
def test_capture_receipt_requires_committed_matching_main_inputs(tmp_path: Path, invalid: str | None) -> None:
    """
    Accept one complete snapshot, then refuse tags whose data or publication lineage changed.

    Args:
        tmp_path (Path): Isolated Git repository with no network remote.
        invalid (str | None): Deliberate violation, or None for an accepted snapshot.

    Returns:
        None: Only a committed matching capture can pass release verification.
    """
    git(tmp_path, "init", "-b", "main")
    (tmp_path / "resumeme.config.yaml").write_text("linkedin: {username: fixture}\n")
    git(tmp_path, "add", "resumeme.config.yaml")
    git(tmp_path, "commit", "-m", "reviewed source")
    source = git(tmp_path, "rev-parse", "HEAD")
    profile = tmp_path / "data/profile.json"
    media = tmp_path / "data/assets/image.png"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"synthetic image")
    save_profile(Profile("fixture", "Example", images=[Media("https://example.test/image", path="data/assets/image.png")]), profile)
    env = dict(
        os.environ,
        GITHUB_REPOSITORY="example/resume",
        GITHUB_REF="refs/heads/main",
        SOURCE_SHA=source,
        GITHUB_RUN_ID="100",
        GITHUB_RUN_ATTEMPT="1",
    )
    command = [sys.executable, str(ROOT / "scripts/ci/linkedin/profile-artifact.py")]
    subprocess.run([*command, "accept"], cwd=tmp_path, env=env, check=True, capture_output=True)
    git(tmp_path, "commit", "-m", "Accepted capture\n\nResumeme-Publication: true")
    git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    receipt = profile.with_suffix(".capture.json")
    if invalid == "missing":
        receipt.unlink()
    elif invalid == "profile":
        save_profile(Profile("fixture", "Changed"), profile)
    elif invalid == "media":
        media.write_bytes(b"changed image")
    elif invalid == "repository":
        env["GITHUB_REPOSITORY"] = "other/fork"
    elif invalid == "uncommitted":
        receipt.write_text(receipt.read_text() + "\n")
    elif invalid == "ancestry":
        git(tmp_path, "update-ref", "refs/remotes/origin/main", source)
    result = subprocess.run([*command, "verify"], cwd=tmp_path, env=env, capture_output=True, text=True, check=False)
    assert (result.returncode == 0) == (invalid is None), result.stderr


@pytest.mark.parametrize("result", ["success", "failure", "skipped", "cancelled"])
@pytest.mark.parametrize("job", ["verified", "release-stage", "pypi-stage", "container-stage", "tag-publish-stage", "tag-pages-stage"])
def test_tag_delivery_rejects_missing_required_publishers(result: str, job: str) -> None:
    """
    Execute the terminal gate against successful verification with an absent or failed publisher.

    Args:
        result (str): Actual GitHub job outcome.
        job (str): Required stage whose result varies.

    Returns:
        None: A green tag requires all intended delivery stages.
    """
    needs = {
        name: {"result": "success"}
        for name in ["source", "verified", "release-stage", "pypi-stage", "container-stage", "tag-publish-stage", "tag-pages-stage"]
    }
    needs[job]["result"] = result
    completed = subprocess.run(
        ["bash", "scripts/release/check-delivery.sh"],
        cwd=ROOT,
        env=dict(os.environ, RESULTS_JSON=json.dumps(needs), RELEASE_TAG="v1.2.3"),
        capture_output=True,
        check=False,
    )
    assert (completed.returncode == 0) == (result == "success")


def test_tag_publishers_override_implicit_skip_but_require_verification() -> None:
    """
    Keep the regression guard on the actual job conditions and the terminal release audit.

    Returns:
        None: Skipped optional ancestors cannot suppress otherwise eligible publishers.
    """
    jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]
    for name in ["release-stage", "container-stage", "pypi-stage"]:
        condition = jobs[name]["if"]
        assert "!cancelled()" in condition
        assert "needs.verified.result == 'success'" in condition
        assert "needs.source.result == 'success'" in condition
        assert "github.event_name == 'push'" in condition
        assert "startsWith(github.ref, 'refs/tags/" in condition
    assert "always()" in jobs["release-check-stage"]["if"]
    build = yaml.safe_load((ROOT / ".github/workflows/stage-build.yml").read_text())["jobs"]["package"]
    assert any("profile-artifact.py verify" in step.get("run", "") for step in build["steps"])


@pytest.mark.parametrize("fault", [None, "missing-release", "missing-asset", "wrong-key", "wrong-source", "bad-signature"])
def test_public_release_audit_downloads_and_checks_delivery(tmp_path: Path, fault: str | None) -> None:
    """
    Exercise release download and verification failures at external command boundaries.

    Args:
        tmp_path (Path): Temporary command fixtures and public attachment directory.
        fault (str | None): Injected delivery defect; None represents a valid command sequence.

    Returns:
        None: Missing or mismatched releases fail, and signature verification is actually invoked on matching files.
    """
    expected, published, commands = [tmp_path / name for name in ("expected", "published", "bin")]
    for directory in (expected, published, commands):
        directory.mkdir()
    artifacts = [
        "resume.pdf",
        "resume.pdf.sig",
        "resume.pdf.sigstore.json",
        "cosign.pub",
        "key-fingerprint.txt",
        "source.json",
        "SHA256SUMS",
        "SHA256SUMS.sigstore.json",
    ]
    source = "a" * 40
    for artifact in artifacts:
        content = json.dumps({"source_commit": source}) if artifact == "source.json" else "synthetic " + artifact
        (expected / artifact).write_text(content)
        (published / artifact).write_text(content)
    if fault == "missing-asset":
        (published / "resume.pdf.sig").unlink()
    elif fault == "wrong-key":
        (published / "cosign.pub").write_text("unexpected key")
    elif fault == "wrong-source":
        (published / "source.json").write_text(json.dumps({"source_commit": "b" * 40}))

    # Stub transport and cryptographic commands only; the production audit owns sequencing and comparisons.
    gh = commands / "gh"
    gh.write_text(f"""#!{sys.executable}
import json, os, pathlib, shutil, sys
if sys.argv[2] == "view":
    if os.environ.get("FAULT") == "missing-release":
        sys.exit(1)
    print(json.dumps({{"isDraft": False, "tagName": "v1.2.3"}}))
else:
    target = pathlib.Path(sys.argv[sys.argv.index("--dir") + 1])
    for path in pathlib.Path(os.environ["PUBLISHED"]).iterdir():
        shutil.copyfile(path, target / path.name)
""")
    cosign = commands / "cosign"
    cosign.write_text("""#!/usr/bin/env bash
printf '%s\\n' "$*" >>"$VERIFICATION_CALLS"
[ "${FAULT:-}" != bad-signature ]
""")
    shasum = commands / "shasum"
    shasum.write_text("#!/usr/bin/env bash\nexit 0\n")
    for executable in commands.iterdir():
        executable.chmod(0o700)
    env = dict(
        os.environ,
        PATH=f"{commands}{os.pathsep}{os.environ['PATH']}",
        PUBLISHED=str(published),
        RELEASE_TAG="v1.2.3",
        SOURCE_SHA=source,
        GITHUB_REPOSITORY="example/resume",
        FAULT=fault or "",
        RETRY_ATTEMPTS="1",
        RETRY_BACKOFF_SECONDS="0",
        RUNNER_TEMP=str(tmp_path),
        VERIFICATION_CALLS=str(tmp_path / "calls"),
    )
    result = subprocess.run(
        ["bash", "scripts/release/check-published.sh", str(expected)], cwd=ROOT, env=env, capture_output=True, text=True, check=False
    )
    assert (result.returncode == 0) == (fault is None), result.stderr
    assert not list(tmp_path.glob("resumeme-published.*"))
    if fault is None:
        calls = (tmp_path / "calls").read_text().splitlines()
        assert len(calls) == 2
        assert all(call.startswith("verify-blob --key ") for call in calls)
