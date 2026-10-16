"""
Maintain fork-relative HTML plot regions without changing surrounding README prose.
"""

from __future__ import annotations

import posixpath
from pathlib import Path
from urllib.parse import quote

from resumeme.exceptions import PublicationError

__all__ = ["markup", "replace_region"]
_START = "<!-- resumeme:convergence:start -->"
_END = "<!-- resumeme:convergence:end -->"


def markup(destination: str | None, readme: str) -> str:
    """
    Build a managed HTML image and evidence link relative to any README destination.

    Args:
        destination (str | None): Repository-relative SVG path, or None to disable publication.
        readme (str): Repository-relative Markdown path containing the block.

    Returns:
        str: Marked HTML with fork-local links, or an empty marked region when disabled.
    """
    body = ""
    if destination is not None:
        directory = Path(readme).parent.as_posix()
        image = quote(posixpath.relpath(destination, directory), safe="/")
        evidence = quote(posixpath.relpath(str(Path(destination).with_suffix(".json")), directory), safe="/")
        body = (
            f'<p><a href="{evidence}"><img src="{image}" alt="Collection convergence across tagged captures: '
            'PID prediction error, worker imbalance and traversal time" width="1000"></a></p>\n'
            "<p>Measured on complete tagged captures; lower error means more accurate timing predictions. "
            "Cold starts have no prediction error point. Click the plot for its source and run evidence.</p>\n"
        )
    return f"{_START}\n{body}{_END}"


def replace_region(original: str, destination: str | None, readme: str) -> str:
    """
    Refresh only an explicitly marked region in an existing landing page.

    Args:
        original (str): Existing Markdown, including any user-authored content.
        destination (str | None): Configured figure path, or None to clear the region.
        readme (str): Markdown destination used to resolve relative links.

    Returns:
        str: Updated Markdown or unchanged text when no region is present.

    Raises:
        PublicationError: Markers are incomplete, repeated or reversed.
    """
    if _START not in original and _END not in original:
        return original
    if original.count(_START) != 1 or original.count(_END) != 1 or original.index(_END) < original.index(_START):
        raise PublicationError("Convergence requires one ordered pair of README HTML markers.")
    start, end = original.index(_START), original.index(_END) + len(_END)
    return original[:start] + markup(destination, readme) + original[end:]
