<!--
    Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at
        http://www.apache.org/licenses/LICENSE-2.0
    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.
-->

# Contributing to flip-utils

For general contribution guidelines (coding style, testing, pull requests), see the
[root CONTRIBUTING.md](../CONTRIBUTING.md).

`flip-utils` is the only component of the mono-repo that is **published as a package** — it goes to
[PyPI as `flip-utils`](https://pypi.org/project/flip-utils/) and is imported as `from flip import ...` by
user-uploaded training code. That gives it a release path of its own, on top of the platform release described in the
root guide. This file documents that path.

## Local development setup

```bash
uv sync --all-extras
make unit-test        # ruff --fix, then pytest with coverage
```

`make unit-test` is the same lint + test pair the release workflow runs before publishing, so a green local run is a
good predictor of a green release. See [`README.md`](README.md) for package structure, job types, and development mode.

## Versioning

The single source of truth is `__version__` in [`flip/__init__.py`](flip/__init__.py). The package metadata declares
`dynamic = ["version"]` in [`pyproject.toml`](pyproject.toml), so the build backend reads that value — there is no
second version string to keep in step inside this directory.

The version follows [Semantic Versioning](https://semver.org/), must be strictly `MAJOR.MINOR.PATCH` (no suffixes),
and must be **strictly greater** than the highest existing `v*.*.*` git tag. Both rules are enforced in CI by
[`check-version-bump.yml`](../.github/workflows/check-version-bump.yml) on every `develop` → `main` PR that touches
`flip-utils/**`.

This version is **not** the same as the FLIP platform version in the root [`pyproject.toml`](../pyproject.toml), and
the two are not required to move together — see [Two version sources](#two-version-sources-two-tag-namespaces) below
for how they interact.

## Checks on a `develop` → `main` PR

| Workflow | Trigger | What it enforces |
| --- | --- | --- |
| [`check-version-bump.yml`](../.github/workflows/check-version-bump.yml) | PR to `main` from `develop`, paths `flip-utils/**` | `__version__` is valid semver and higher than the latest `v*.*.*` tag |
| [`check-package-metadata.yml`](../.github/workflows/check-package-metadata.yml) | PR to `develop` or `main`, paths `flip-utils/**` | `uv build` succeeds and `twine check --strict` passes on the artifacts |
| [`pr-release-notes-preview.yml`](../.github/workflows/pr-release-notes-preview.yml) | PR to `main` from `develop` | Posts / updates the release-notes preview comment (see below) |

The two `main`-only workflows are gated on `github.event.pull_request.head.ref == 'develop'`, so they run on the
release PR itself and nowhere else.

## Releasing to PyPI

Publishing is automatic on merge to `main`. [`release-pypi.yml`](../.github/workflows/release-pypi.yml) runs from
`./flip-utils` and:

1. Extracts `__version__` from `flip/__init__.py`.
2. **Skips the whole release** if the tag `flip-utils-v<VERSION>` already exists — so an unbumped version is a no-op,
   not a failure, and merges to `main` that do not touch this package cost nothing.
3. Runs `uv sync --all-extras`, `ruff check .`, and `pytest`.
4. Builds with `uv build` and publishes to PyPI with `uv publish --trusted-publishing always` — OIDC trusted
   publishing against the `flip` GitHub environment, so no PyPI token is stored as a repository secret.
5. Pushes the annotated tag `flip-utils-v<VERSION>`.
6. Composes the release notes (below) and creates the GitHub Release titled `flip-utils v<VERSION>`, attaching the
   built `dist/*` wheel and sdist.

The job is skipped entirely on forks (`if: github.repository == 'londonaicentre/FLIP'`), which cannot publish or push
tags upstream.

### Release notes

There is no `CHANGELOG.md` — the [Releases page](https://github.com/londonaicentre/FLIP/releases) is the changelog.
Notes are assembled from two pieces:

- **Header** — [`.github/RELEASE_NOTES_TEMPLATE.md`](../.github/RELEASE_NOTES_TEMPLATE.md), with its license comment
  block stripped and `{{VERSION}}`, `{{TAG}}`, `{{PREV_TAG}}` substituted. This is where Highlights, Breaking Changes,
  New Features, and Bug Fixes live.
- **Changelog** — GitHub's `releases/generate-notes` API, diffing the new tag against the previous
  `flip-utils-v*.*.*` tag, so the range spans this package's own release history rather than the platform's. It
  lists every merged PR in the range plus a contributors section, categorised by PR label according to
  [`.github/release.yml`](../.github/release.yml).

Because the header comes from the template **file**, curating a release means editing
`.github/RELEASE_NOTES_TEMPLATE.md` on the release branch before the PR merges. Editing the preview comment on the PR
has no effect — it is regenerated from the file on every push.

The preview comment posted by `pr-release-notes-preview.yml` renders exactly this combination ahead of the merge. It
is keyed by a `<!-- release-notes-preview -->` marker and updated in place, so the PR carries one comment that always
reflects the current head.

### Manual publishing

[`release.sh`](release.sh) is a local fallback for the CI path — use it only when the workflow cannot run (for
example, to validate a build against TestPyPI before a release). It authenticates with a `UV_PUBLISH_TOKEN` PyPI API
token rather than OIDC, and it neither tags nor creates a GitHub Release, so a manual publish leaves the repository
without the release metadata the CI path produces.

```bash
./flip-utils/release.sh --dry-run                       # build + verify, no upload
UV_PUBLISH_TOKEN=pypi-... ./flip-utils/release.sh --test # publish to TestPyPI
UV_PUBLISH_TOKEN=pypi-... ./flip-utils/release.sh        # publish to PyPI
```

Two behaviours to be aware of before reaching for it:

- It **requires the root `pyproject.toml` version and `flip/__version__` to be identical**, and aborts with a version
  mismatch otherwise. The CI path applies no such rule, so the two versions legitimately diverge in normal operation
  and the script will refuse to run whenever they have.
- It `cd`s to the repository root before building, so `uv build` there builds the root `[project] name = "flip"`
  package rather than this directory's `flip-utils` distribution. Prefer `uv build` from `flip-utils/` — which is what
  the workflow does — if you need artifacts to inspect.

## Two version sources, two tag namespaces

Merging to `main` runs two independent release workflows, each with its own tag namespace:

| Workflow | Reads version from | Tag | Produces |
| --- | --- | --- | --- |
| [`release.yml`](../.github/workflows/release.yml) | root [`pyproject.toml`](../pyproject.toml) | `v<X.Y.Z>` | tag + GitHub Release named `Release v<X.Y.Z>` |
| [`release-pypi.yml`](../.github/workflows/release-pypi.yml) | [`flip/__init__.py`](flip/__init__.py) | `flip-utils-v<X.Y.Z>` | PyPI publish + tag + GitHub Release named `flip-utils v<X.Y.Z>` |

Each skips when its own tag already exists, so in the common case only one fires and the other is a no-op: bump only
the root version and you get a platform release; bump only `__version__` and you get a package release.

The `flip-utils-` prefix is what keeps the two apart, and it is load-bearing: a `git tag --list 'v*.*.*'` lookup
matches platform tags only. **Do not drop the prefix or widen those globs** — the two version sequences advance
independently and will overlap. Because the namespaces are disjoint, bumping both versions in one release PR is safe:
neither workflow can claim the other's tag or suppress its release, even when the numbers happen to match. See
[Two release trains, two tag namespaces](../CONTRIBUTING.md#two-release-trains-two-tag-namespaces) in the root guide
for the platform-side view.

## Documentation

[`docs/overview.rst`](docs/overview.rst) does double duty. It is the package's `readme` in
[`pyproject.toml`](pyproject.toml), so it becomes the **PyPI project page** on publish, and it is `include`d verbatim
by [`docs/source/working-with-flip-apps/flip-utils-package.rst`](../docs/source/working-with-flip-apps/flip-utils-package.rst),
so it is also a page of the [FLIP documentation](https://londonaicentreflip.readthedocs.io/). Edit it once and both
surfaces follow — but keep it readable in both, and keep it valid reStructuredText: `check-package-metadata.yml` runs
`twine check --strict`, which fails the PR if the rendered long description is malformed.

Public API changes should also land in the root Sphinx docs, which autodoc this package directly —
[`docs/source/conf.py`](../docs/source/conf.py) puts `flip-utils/` on `sys.path` and points the API docs at
`flip-utils/flip`.
