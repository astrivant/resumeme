The heading inventory in `contents.py` is adapted from Polyad's
[documentation updater at c446fa5](https://github.com/astrivant/polyad/blob/c446fa5f0dc8eaaec82b49e864e8ca86046c53d1/scripts/documentation/update-contents.py),
licensed under GPL-3.0-only. The original license is retained in
[scripts/licenses/GPL-3.0-only.txt](../licenses/GPL-3.0-only.txt). The local contents
pass preserves existing placement,
limits navigation to heading levels two and three, and discovers all READMEs.

Run `python scripts/documentation/contents.py` to regenerate; the local and CI
pre-commit hook runs the same script with `--check`. Review and stage its output.
