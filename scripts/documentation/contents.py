"""
Maintain README navigation without moving authored introductions or plot regions.
"""

from __future__ import annotations

import argparse
import html
import re
import subprocess
from pathlib import Path

START = "<!-- toc:start -->"
END = "<!-- toc:end -->"


def heading_inventory(document: str) -> list[tuple[int, int, str, str]]:
    """
    Find headings and allocate matching, unique Markdown and PDF destinations.

    Args:
        document (str): Markdown source without a generated contents block.

    Returns:
        list[tuple[int, int, str, str]]: Line index, heading level, visible label, and unique anchor.
    """
    lines = document.splitlines(keepends=True)
    headings: list[tuple[int, int, str, str]] = []
    used: set[str] = set()
    fence = ""
    comment = False
    for index, line in enumerate(lines):
        if fence:
            if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*", line):
                fence = ""
            continue
        visible = line
        if comment:
            if "-->" not in visible:
                continue
            visible = visible.split("-->", 1)[1]
            comment = False
        visible = re.sub(r"<!--.*?-->", "", visible)
        if "<!--" in visible:
            visible = visible.split("<!--", 1)[0]
            comment = True
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", visible)
        if marker:
            fence = marker[1]
            continue
        heading = re.match(r"^ {0,3}(#{1,6})[ \t]+(.+?)\s*$", visible)
        if heading is None:
            continue
        label = re.sub(r"\s+#+\s*$", "", heading[2])
        label = re.sub(r"!?\[([^\]]+)\]\([^)]*\)", r"\1", label)
        label = html.unescape(re.sub(r"<[^>]+>", "", label))
        label = re.sub(r"[`*~]", "", label)
        label = re.sub(r"(?<!\w)_([^_]+)_(?!\w)", r"\1", label)
        base = re.sub(r"[^\w\- ]", "", label.lower()).replace(" ", "-")
        anchor = base
        suffix = 0
        while anchor in used:
            suffix += 1
            anchor = f"{base}-{suffix}"
        used.add(anchor)
        headings.append((index, len(heading[1]), label, anchor))
    return headings


def with_contents(document: str) -> str:
    """
    Refresh an existing contents section in place, or insert navigation before the first section.

    Args:
        document (str): Authored README with optional generated navigation.

    Returns:
        str: Markdown with depth-three contents and unchanged prose outside its managed region.

    Raises:
        ValueError: Generated markers are malformed.
    """
    lines = document.splitlines(keepends=True)
    headings = heading_inventory(document)
    contents = next((item for item in headings if item[2].casefold() in {"contents", "table of contents"}), None)
    sections = [item for item in headings if 2 <= item[1] <= 3 and item != contents]
    if not sections:
        return document
    entries = "\n".join(f"{'  ' * (level - 2)}- [{label}](#{anchor})" for _, level, label, anchor in sections)
    block = f"{START}\n{entries}\n{END}\n\n"
    if START in document or END in document:
        if document.count(START) != 1 or document.count(END) != 1 or document.index(END) < document.index(START):
            raise ValueError("README requires one ordered pair of contents markers.")
        start, end = document.index(START), document.index(END) + len(END)
        return document[:start] + block.rstrip() + document[end:]
    if contents is not None:
        start = contents[0] + 1
        end = start
        while end < len(lines) and (not lines[end].strip() or re.match(r"^\s*[-*+] \[.*\]\(.*\)\s*$", lines[end])):
            end += 1
        return "".join(lines[:start]) + "\n" + block + "".join(lines[end:])
    start = sections[0][0]
    return "".join(lines[:start]) + "## Contents\n\n" + block + "".join(lines[start:])


def main() -> int:
    """
    Check or regenerate all tracked and untracked READMEs in the repository.

    Returns:
        int: Nonzero only when check mode finds stale navigation.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    names = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "*.md"], capture_output=True, text=True, check=True
    ).stdout.split("\0")
    changed = False
    for name in sorted(set(names)):
        path = Path(name)
        if path.name.casefold() != "readme.md" or not path.is_file():
            continue
        original = path.read_text(encoding="utf-8")
        updated = with_contents(original)
        if updated != original:
            changed = True
            if args.check:
                print(f"Outdated contents: {path}")
            else:
                path.write_text(updated, encoding="utf-8")
    return int(args.check and changed)


if __name__ == "__main__":
    raise SystemExit(main())
