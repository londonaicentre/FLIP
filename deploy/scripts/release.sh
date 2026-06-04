#!/usr/bin/env bash
# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# release.sh — Build and publish the flip package to PyPI (or TestPyPI)
#
# Usage:
#   ./deploy/release.sh [OPTIONS]
#
# Options:
#   --test         Publish to TestPyPI instead of PyPI
#   --dry-run      Build and verify without uploading
#   --skip-tests   Skip linting and unit tests (not recommended)
#   --help         Show this message
#
# Required environment variables (unless --dry-run):
#   UV_PUBLISH_TOKEN   PyPI (or TestPyPI) API token
#
# Example — publish to TestPyPI first, then PyPI:
#   UV_PUBLISH_TOKEN=pypi-... ./deploy/release.sh --test
#   UV_PUBLISH_TOKEN=pypi-... ./deploy/release.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── Defaults ──────────────────────────────────────────────────────────────────
TARGET="pypi"
DRY_RUN=false
SKIP_TESTS=false

PYPI_PUBLISH_URL="https://upload.pypi.org/legacy/"
TESTPYPI_PUBLISH_URL="https://test.pypi.org/legacy/"

# ── Argument parsing ──────────────────────────────────────────────────────────
for arg in "$@"; do
    case "$arg" in
        --test)       TARGET="testpypi" ;;
        --dry-run)    DRY_RUN=true ;;
        --skip-tests) SKIP_TESTS=true ;;
        --help)
            sed -n '/^# Usage:/,/^[^#]/{ /^[^#]/d; s/^# \{0,1\}//; p }' "$0"
            exit 0
            ;;
        *) echo "Unknown option: $arg. Use --help for usage." >&2; exit 1 ;;
    esac
done

# ── Helpers ───────────────────────────────────────────────────────────────────
info()  { echo "[INFO]  $*"; }
warn()  { echo "[WARN]  $*" >&2; }
error() { echo "[ERROR] $*" >&2; exit 1; }

# ── Preflight checks ─────────────────────────────────────────────────────────
cd "$REPO_ROOT"

command -v uv >/dev/null 2>&1 || error "'uv' is not installed or not on PATH."

VERSION=$(grep -m1 '^version' pyproject.toml | sed 's/.*= *"\(.*\)"/\1/')
INIT_VERSION=$(grep -m1 '__version__' flip/__init__.py | sed 's/.*= *"\(.*\)"/\1/')

info "Package:  flip"
info "Version:  ${VERSION}"
info "Target:   ${TARGET}"
info "Dry run:  ${DRY_RUN}"

if [[ "$VERSION" != "$INIT_VERSION" ]]; then
    error "Version mismatch: pyproject.toml has '${VERSION}' but flip/__init__.py has '${INIT_VERSION}'. Align them before releasing."
fi

if [[ "$DRY_RUN" == false && -z "${UV_PUBLISH_TOKEN:-}" ]]; then
    error "UV_PUBLISH_TOKEN is not set. Export your PyPI API token or use --dry-run."
fi

# ── Lint & test ───────────────────────────────────────────────────────────────
if [[ "$SKIP_TESTS" == false ]]; then
    info "Running linter (ruff)..."
    uv run ruff check .

    info "Running unit tests..."
    uv run pytest -s -vv
else
    warn "Skipping linting and tests (--skip-tests)."
fi

# ── Build ─────────────────────────────────────────────────────────────────────
info "Cleaning previous build artifacts..."
rm -rf dist/

info "Building wheel and sdist..."
uv build

info "Build artifacts:"
ls -lh dist/

# ── Publish ───────────────────────────────────────────────────────────────────
if [[ "$DRY_RUN" == true ]]; then
    info "Dry run — skipping upload."
    info "To upload, run without --dry-run and with UV_PUBLISH_TOKEN set."
    exit 0
fi

if [[ "$TARGET" == "testpypi" ]]; then
    PUBLISH_URL="$TESTPYPI_PUBLISH_URL"
    CHECK_URL="https://test.pypi.org/simple/"
    info "Publishing to TestPyPI..."
else
    PUBLISH_URL="$PYPI_PUBLISH_URL"
    CHECK_URL="https://pypi.org/simple/"
    info "Publishing to PyPI..."
fi

uv publish \
    --publish-url "$PUBLISH_URL" \
    --check-url "$CHECK_URL" \
    --token "$UV_PUBLISH_TOKEN"

info "Done. flip==${VERSION} published to ${TARGET}."

if [[ "$TARGET" == "testpypi" ]]; then
    info "Verify: pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ flip==${VERSION}"
else
    info "Verify: pip install flip==${VERSION}"
fi
