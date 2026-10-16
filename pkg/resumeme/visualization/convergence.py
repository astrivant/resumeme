"""
Plot aggregate, pre-update capture errors without publishing profile or route identities.
"""

from __future__ import annotations

import json
import math
import re
from statistics import fmean, pstdev
from typing import TYPE_CHECKING

import cattrs
import matplotlib
from attrs import asdict, frozen

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from resumeme.linkedin.capture.timings import learn_timings

if TYPE_CHECKING:
    from pathlib import Path

    from resumeme.linkedin.capture.shards import CapturePlan, CaptureShard

__all__ = ["Observation", "decode_history", "measure", "merge_history", "render", "serialize"]
_CONVERTER = cattrs.Converter(forbid_extra_keys=True)
_LIMIT = 60


@frozen
class Observation:
    """
    Retain one tag's aggregate evidence, without profile content or per-route measurements.

    Attributes:
        repository (str): Publishing owner/repository, which isolates inherited fork history.
        scope (str): Opaque fingerprint of capture owner, settings and runner class.
        tag (str): Tag whose capture produced this observation.
        source (str): Immutable source revision used by that pipeline.
        run (int): GitHub Actions run identifier.
        attempt (int): Attempt number, used to reject older retries.
        workers (int): Number of accepted capture workers, including idle workers.
        routes (int): Number of measured traversal units.
        calibrated (int): Units with a prior PID state and a frozen estimate.
        error_percent (float | None): Sum of absolute pre-update errors divided by observed calibrated duration, as a percentage.
        dispersion_percent (float): Population standard deviation divided by mean worker traversal time, as a percentage.
        peak_seconds (float): Largest worker traversal sum, excluding login, startup, queueing and PDF work.
    """

    repository: str
    scope: str
    tag: str
    source: str
    run: int
    attempt: int
    workers: int
    routes: int
    calibrated: int
    error_percent: float | None
    dispersion_percent: float
    peak_seconds: float


def measure(
    plan: CapturePlan, shards: list[CaptureShard], *, repository: str, scope: str, tag: str, source: str, run: int, attempt: int
) -> Observation:
    """
    Compare measurements with the immutable estimates actually supplied to workers.

    Args:
        plan (CapturePlan): Frozen plan for this accepted capture.
        shards (list[CaptureShard]): Complete workers, including idle workers.
        repository (str): Current GitHub repository.
        scope (str): Fingerprint of the capture configuration.
        tag (str): Current release tag.
        source (str): Tagged source commit.
        run (int): Actions run identifier.
        attempt (int): Actions attempt number.

    Returns:
        Observation: Validated aggregate timing evidence.

    Raises:
        ValueError: Measurements are missing, incomplete, or invalid.
    """
    # Reuse strict fan-in and timing validation before projecting the small public allowlist.
    if learn_timings(plan, shards) is None:
        raise ValueError("Convergence requires complete traversal measurements.")

    observed = {key: value for shard in shards for key, value in shard.route_seconds.items()}
    loads = [sum(shard.route_seconds.values()) for shard in shards]
    calibrated = [route for route in plan.routes if route.feedback is not None and route.estimated_seconds is not None]
    denominator = sum(observed[route.unit_key] for route in calibrated)
    error = sum(abs(observed[route.unit_key] - float(route.estimated_seconds or 0)) for route in calibrated)
    sample = Observation(
        repository,
        scope,
        tag,
        source,
        run,
        attempt,
        plan.shard_count,
        len(observed),
        len(calibrated),
        100 * error / denominator if denominator else None,
        100 * pstdev(loads) / fmean(loads) if any(loads) else 0.0,
        max(loads),
    )
    decode_history(serialize([sample]))
    return sample


def serialize(samples: list[Observation]) -> bytes:
    """
    Encode a portable, inspectable history with a versioned aggregate-only schema.

    Args:
        samples (list[Observation]): Ordered accepted observations.

    Returns:
        bytes: Deterministic UTF-8 JSON with no variable timestamps.
    """
    return (
        json.dumps({"version": 1, "samples": [asdict(sample) for sample in samples]}, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode()


def decode_history(data: bytes) -> list[Observation]:
    """
    Reject malformed history instead of silently presenting a reset as convergence.

    Args:
        data (bytes): Aggregate history from Git or the current run's artifact.

    Returns:
        list[Observation]: Valid observations in their stored order.

    Raises:
        ValueError: The schema, bounds or values are invalid.
    """
    if len(data) > 256 * 1024:
        raise ValueError("Convergence history exceeds 256 KiB.")

    value = json.loads(data)
    if not isinstance(value, dict) or set(value) != {"version", "samples"} or value["version"] != 1:
        raise ValueError("Unsupported convergence history schema.")

    try:
        samples = _CONVERTER.structure(value["samples"], list[Observation])
    except cattrs.BaseValidationError as error:
        raise ValueError("Invalid convergence observations.") from error

    if len(samples) > _LIMIT or len({(item.repository, item.scope, item.tag) for item in samples}) != len(samples):
        raise ValueError("Convergence history must contain at most 60 distinct tags.")

    for item in samples:
        numbers = [item.dispersion_percent, item.peak_seconds]
        if item.error_percent is not None:
            numbers.append(item.error_percent)
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+", item.repository)
            or not re.fullmatch(r"[a-f0-9]{64}", item.scope)
            or not re.fullmatch(r"[a-f0-9]{40}", item.source)
            or not item.tag
            or len(item.tag) > 128
            or any(ord(char) < 32 for char in item.tag)
            or min(item.run, item.attempt, item.workers) < 1
            or not 0 <= item.calibrated <= item.routes
            or (item.error_percent is None) != (item.calibrated == 0)
            or any(not math.isfinite(number) or number < 0 for number in numbers)
        ):
            raise ValueError("Convergence observations contain invalid metadata or measurements.")
    return samples


def merge_history(history: list[Observation], current: Observation) -> list[Observation]:
    """
    Keep compatible fork-local history and make tag reruns replace their original point.

    Args:
        history (list[Observation]): Previously published observations.
        current (Observation): Complete measurements from the current run.

    Returns:
        list[Observation]: At most sixty observations ordered by Actions run identifier.

    Raises:
        ValueError: A published tag was moved to a different source revision.
    """
    compatible = [item for item in history if (item.repository, item.scope) == (current.repository, current.scope)]
    previous = next((item for item in compatible if item.tag == current.tag), None)
    if previous is not None:
        if previous.source != current.source:
            raise ValueError("Cannot replace convergence evidence for a moved tag.")
        if (previous.run, previous.attempt) > (current.run, current.attempt):
            return compatible
    result = sorted([item for item in compatible if item.tag != current.tag] + [current], key=lambda item: item.run)[-_LIMIT:]
    decode_history(serialize(result))
    return result


def render(samples: list[Observation], output: Path) -> None:
    """
    Render measured errors and scheduling balance with an explicit empty or cold state.

    Args:
        samples (list[Observation]): Validated observations in capture order.
        output (Path): SVG or PNG destination, selected by the caller.

    Returns:
        None: Matplotlib writes the figure and closes its owned resources.
    """
    decode_history(serialize(samples))
    with plt.rc_context(
        {"svg.hashsalt": "resumeme-convergence", "svg.fonttype": "none", "font.family": "DejaVu Sans", "text.usetex": False}
    ):
        figure, axes = plt.subplots(1, 3, figsize=(13, 4.5))
        figure.suptitle("Collection convergence", fontsize=18, color="#38654B", y=0.98)
        figure.text(0.5, 0.89, "Are timing predictions and worker balance improving across tagged captures?", ha="center", fontsize=11)
        metrics: tuple[tuple[list[float | None], str, str, str], ...] = (
            ([item.error_percent for item in samples], "PID prediction error", "Weighted absolute error (%)", "#C35C24"),
            ([item.dispersion_percent for item in samples], "Worker imbalance", "Traversal dispersion (%)", "#38654B"),
            ([item.peak_seconds for item in samples], "Slowest worker", "Traversal time (seconds)", "#24688A"),
        )
        for axis, (values, title, label, color) in zip(axes, metrics, strict=True):
            axis.set_title(title, fontsize=12)
            axis.set_ylabel(label)
            axis.set_xlabel("Tagged capture (oldest to newest)")
            axis.spines[["top", "right"]].set_visible(False)
            axis.grid(axis="y", alpha=0.2)
            if samples:
                axis.plot(range(1, len(samples) + 1), [float("nan") if value is None else value for value in values], "o-", color=color)
                axis.xaxis.get_major_locator().set_params(integer=True)
                axis.set_xlim(0.5, len(samples) + 0.5)
            else:
                axis.text(0.5, 0.5, "Awaiting first tagged capture", ha="center", transform=axis.transAxes)
                axis.set_xticks([])
            # Set the lower bound after plotting so Matplotlib still fits the upper bound to actual data.
            axis.set_ylim(bottom=0)
        if samples and all(item.error_percent is None for item in samples):
            axes[0].text(0.5, 0.5, "Cold start: no prior PID estimates", ha="center", transform=axes[0].transAxes, fontsize=9)
        caption = "Only complete captures. Cold starts have no error point. Traversal excludes startup, login and queueing."
        if samples:
            last = samples[-1]
            caption += (
                f"\nLatest: {last.workers} workers; {last.calibrated}/{last.routes} routes calibrated. "
                "Source and run identifiers are in the adjacent JSON."
            )
        figure.text(0.5, 0.04, caption, ha="center", fontsize=9, color="#555555")
        figure.tight_layout(rect=(0, 0.13, 1, 0.85))
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            figure.savefig(output, metadata={"Date": None} if output.suffix == ".svg" else {})
        finally:
            plt.close(figure)
        if output.suffix == ".svg":
            # Matplotlib emits trailing spaces inside path attributes; normalize only line ends for clean Git diffs.
            output.write_text("\n".join(line.rstrip() for line in output.read_text(encoding="utf-8").splitlines()) + "\n", encoding="utf-8")
