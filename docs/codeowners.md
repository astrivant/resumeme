# Keep CODEOWNERS aligned with the repository

Add review ownership and normalize it in the same change. The repository-local
compiler groups literal paths by directory and removes redundant rules while
preserving ownership precedence. It uses the project development environment;
there is no external service or runtime package dependency.

```bash
poetry run pre-commit install
poetry run python -m scripts.codeowners.cli --write
poetry run pre-commit run --all-files
poetry run pre-commit run codeowners-tree-push --hook-stage pre-push --all-files
```

Installation enables both commit and push hooks. The commit hook normalizes the
whole map, including when only repository paths change. When formatting changes
CODEOWNERS, pre-commit stops: review and stage that diff, then retry. Nothing is
automatically staged.

Push and CI checks read both CODEOWNERS and its tracked inventory from **HEAD**.
An unstaged formatting fix cannot mask a stale commit. When pushing additional
refs, check each with `--check --revision REF`. The separate pinned CODEOWNERS
validator checks syntax, paths and duplicate patterns; it does not verify that
an owner has repository access.

## Small, explicit passes

1. Read the CODEOWNERS text and Git's tracked path inventory. Revision checks read
   both from that revision; working checks use working text and the index inventory.
2. Parse existing root-anchored literal scopes with explicit owner tokens. Keep
   wildcard rules, absent paths, ownerless exceptions, escapes, inline comments,
   GitLab sections and other syntax byte-for-byte as ordering boundaries. Blank
   lines and standalone comments also delimit groups. Report opaque line counts.
3. Remove rules shadowed by a later covering rule and redundant same-owner rules
   already covered by the preceding applicable scope. This pass adds no ownership.
4. Order disjoint paths by directory components. A NetworkX precedence graph keeps
   overlapping scopes in their original relative order, including child overrides.
5. Emit the candidate, retaining comments, owner order and line endings. Check mode
   exits 1 for drift and 2 for invalid input or I/O failures. Write mode preserves
   permissions and replaces text atomically after checking for concurrent edits.

The normal pass preserves matching behavior for existing and future paths in its
supported literal subset. It does not delete absent paths, which may deliberately
reserve future ownership. Boundaries can prevent complete global grouping; semantic
preservation takes priority. This is a normalizer, not a full host syntax validator
or owner eligibility check. It neither changes owners nor grants permissions.

## Consolidate a complete subtree

```bash
poetry run python -m scripts.codeowners.cli --write --consolidate
```

This explicit option combines two or more same-owner scopes when they cover every
tracked file under a directory, with no conflicting rule in the same group. It
preserves current tracked-file ownership but also assigns those owners to future
files in the directory. Each new directory scope is reported for review. Unowned
tracked files, different owners, comments and opaque rules prevent unsafe folding.
Untracked and ignored paths are outside the inventory; no symlink is followed.
Hooks do not opt into this broader policy automatically.

## Reuse and host semantics

Pass `--repo /existing/checkout --file CODEOWNERS` for another existing project.
For GitLab, select its actual ownership file, such as `.gitlab/CODEOWNERS`.
The compiler's literal subset works within either dialect; it never moves rules
across section headers, combines repeated sections, or interprets default owners.
No remote access, credential lookup or new checkout is involved.

Retain platform-compatible ownership validation separately. GitHub's
[last matching rule](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners)
and GitLab's [per-section evaluation](https://docs.gitlab.com/user/project/codeowners/reference/)
make arbitrary sorting unsafe. Pre-commit documents
[hook stage selection](https://pre-commit.com/#confining-hooks-to-run-at-certain-stages)
and `default_install_hook_types` for installing both commit and push gates.

The pure compiler receives explicit text and paths. Its tests compare generated
rule combinations against an independent literal matcher, including future paths,
check idempotence, preserve opaque boundaries, and exercise the real Git-backed
CLI without remote services. Run `pytest -n 0 tests/codeowners` for that contract.
