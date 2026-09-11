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
#
# Guard: the dcm2niix Container Service image pin has one source of truth (FLIP#980).
#
# `ARG DCM2NIIX_VERSION` in trust/xnat/dcm2niix/Dockerfile decides which tag the publish workflow
# pushes; three deploy-time configs then carry the resulting
# `ghcr.io/londonaicentre/xnat-dcm2niix:<version>` string as a literal. Nothing at runtime notices
# when a version bump leaves one of them behind — the old immutable tag simply keeps being pulled,
# and imaging-api's per-project event subscription looks the XNAT command up by that exact string,
# so a half-bumped set silently registers a command nothing ever triggers. This script fails on
# that drift. Wired as a pre-commit hook and as a CI step in .github/workflows/test_trust_xnat.yml.
#
# Usage: trust/xnat/dcm2niix/check_image_pin_sync.sh   (runnable from any working directory)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
DOCKERFILE_REL="trust/xnat/dcm2niix/Dockerfile"
DOCKERFILE="${REPO_ROOT}/${DOCKERFILE_REL}"
IMAGE="ghcr.io/londonaicentre/xnat-dcm2niix"

# Every file that pins the image by its full `<image>:<tag>` string.
REFERENCES=(
    "trust/xnat/xnat/config/dcm2niix_command.json"
    "deploy/providers/kubernetes/templates/xnat-init-job.yaml"
    "trust/imaging-api/imaging_api/config.py"
)

if [[ ! -f "${DOCKERFILE}" ]]; then
    echo "check_image_pin_sync: cannot find ${DOCKERFILE_REL}" >&2
    exit 1
fi

VERSION="$(sed -n 's/^ARG DCM2NIIX_VERSION=//p' "${DOCKERFILE}")"
if [[ -z "${VERSION}" ]]; then
    echo "check_image_pin_sync: could not read 'ARG DCM2NIIX_VERSION=' from ${DOCKERFILE_REL}" >&2
    exit 1
fi

PINNED="${IMAGE}:${VERSION}"
status=0

for reference in "${REFERENCES[@]}"; do
    path="${REPO_ROOT}/${reference}"
    if [[ ! -f "${path}" ]]; then
        echo "check_image_pin_sync: ${reference} is missing (the pin has nowhere to land)" >&2
        status=1
        continue
    fi

    # Count lines carrying the image at any tag, then lines carrying it at the pinned tag. A file
    # that dropped the reference entirely fails on the first count; a file left on an older tag
    # fails on the mismatch. Counting (rather than `! grep -q`) keeps both failures distinct and
    # keeps a partially-bumped file — some lines new, some old — from passing.
    tagged="$(grep -c -F -- "${IMAGE}:" "${path}" || true)"
    pinned_hits="$(grep -c -F -- "${PINNED}" "${path}" || true)"

    if [[ "${tagged}" -eq 0 ]]; then
        echo "check_image_pin_sync: ${reference} no longer references ${IMAGE} at all" >&2
        status=1
    elif [[ "${tagged}" -ne "${pinned_hits}" ]]; then
        echo "check_image_pin_sync: ${reference} does not match ${DOCKERFILE_REL} (${VERSION}):" >&2
        grep -n -F -- "${IMAGE}:" "${path}" | { grep -v -F -- "${PINNED}" || true; } >&2
        status=1
    fi
done

if [[ "${status}" -ne 0 ]]; then
    echo "" >&2
    echo "The dcm2niix image pin must read ${PINNED} in all of:" >&2
    printf '  %s\n' "${DOCKERFILE_REL}" "${REFERENCES[@]}" >&2
    exit 1
fi

echo "check_image_pin_sync: ${PINNED} is consistent across ${DOCKERFILE_REL} and ${#REFERENCES[@]} configs."
