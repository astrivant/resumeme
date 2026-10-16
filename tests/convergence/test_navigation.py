"""
Protect README prose and HTML plots while maintaining nested navigation.
"""

from __future__ import annotations

from scripts.documentation.contents import with_contents


def test_navigation_preserves_intro_plot_and_existing_placement() -> None:
    """
    Refresh nested headings without touching authored prose or managed image markup.

    Returns:
        None: Regeneration is idempotent and headings inside fences do not enter the contents.
    """
    intro = '# Project\n\nIntroduction.\n\n<p><img src="plot.svg"></p>\n\n'
    body = "## Usage\n\n### Run\n\n```markdown\n## Example only\n```\n\n#### Detail\n"
    result = with_contents(intro + "## Contents\n\n- [Old](#old)\n\n" + body)
    assert result.startswith(intro)
    assert result.endswith(body)
    assert "- [Usage](#usage)" in result
    assert "  - [Run](#run)" in result
    assert "(#example-only)" not in result
    assert "(#detail)" not in result
    assert with_contents(result) == result
    assert with_contents('# Personal resume\n\n<p><img src="plot.svg"></p>\n') == '# Personal resume\n\n<p><img src="plot.svg"></p>\n'
