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

# Convert each Cypress-recorded mp4 under test/cypress/videos/ into a GIF under
# docs/source/assets/<bucket>/, where <bucket> is the source spec's parent
# directory name under test/cypress/docs/ (admin, flip, …). The mp4's location
# isn't reliable for this — Cypress collapses common roots, so running only
# flip/ specs produces flat `videos/<spec>.mp4` paths. Resolving the bucket by
# looking the spec back up under test/cypress/docs/ keeps single-bucket runs
# routing correctly. Spec filenames map 1:1 to GIF basenames
# (reset-mfa.spec.ts.mp4 → reset-mfa.gif).

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ui_dir="$(cd "${script_dir}/.." && pwd)"
repo_root="$(cd "${ui_dir}/.." && pwd)"

videos_root="${ui_dir}/test/cypress/videos"
specs_root="${ui_dir}/test/cypress/docs"
assets_root="${repo_root}/docs/source/assets"

# ffmpeg crop window isolating the AUT iframe inside the recorded frame.
# Must stay in lockstep with cypress.docs.config.ts:
#   CROP_WIDTH/HEIGHT == viewportWidth/viewportHeight (1280x800)
#   CROP_X/Y derive from --window-size=1920,1200 in before:browser:launch
# If you change either, re-eyeball with
#   ffmpeg -i <mp4> -vf "select='eq(n,40)'" -vframes 1 /tmp/probe.png
CROP_WIDTH=1280
CROP_HEIGHT=800
CROP_X=540
CROP_Y=80

if [[ ! -d "${videos_root}" ]]; then
    echo "No videos directory at ${videos_root}; nothing to convert." >&2
    exit 0
fi

mapfile -t videos < <(find "${videos_root}" -type f -name "*.mp4" | sort)
if (( ${#videos[@]} == 0 )); then
    echo "No mp4 files under ${videos_root}; nothing to convert." >&2
    exit 0
fi

for mp4 in "${videos[@]}"; do
    base="$(basename "${mp4}")"
    # reset-mfa.spec.ts.mp4 → reset-mfa.spec.ts
    spec_file="${base%.mp4}"
    # reset-mfa.spec.ts → reset-mfa
    name="${spec_file%.spec.ts}"
    # Find the spec on disk under docs/<bucket>/, pick the bucket from its path.
    # A spec basename must be unique across buckets — otherwise the bucket the
    # GIF routes to would depend on find's traversal order. Fail loud instead.
    mapfile -t spec_matches < <(find "${specs_root}" -type f -name "${spec_file}" | sort)
    if (( ${#spec_matches[@]} == 0 )); then
        echo "  skipping ${base}: no matching spec under ${specs_root}/" >&2
        continue
    fi
    if (( ${#spec_matches[@]} > 1 )); then
        echo "  error: ${spec_file} matches multiple specs under ${specs_root}/:" >&2
        printf '    %s\n' "${spec_matches[@]}" >&2
        echo "  spec basenames must be unique across buckets — rename one." >&2
        exit 1
    fi
    spec_path="${spec_matches[0]}"
    bucket="$(basename "$(dirname "${spec_path}")")"
    out_dir="${assets_root}/${bucket}"
    mkdir -p "${out_dir}"
    out="${out_dir}/${name}.gif"
    echo "→ ${out}"
    # cypress.docs.config.ts forces Chrome to --window-size=1920,1200 so the
    # AUT iframe gets enough room to render 1:1 at viewport size (CROP_WIDTH
    # x CROP_HEIGHT) inside the captured 1920x1112 frame, sitting at offset
    # (CROP_X, CROP_Y). The crop below isolates exactly that region; ffmpeg
    # then does a clean downsample to 1200px wide instead of upsampling a
    # 780-wide slice. 15 fps + lanczos downsample + per-clip palette keeps
    # file sizes in the 200 KB–4 MB envelope used by the existing GIFs.
    ffmpeg -y -hide_banner -loglevel error -i "${mp4}" \
        -vf "crop=${CROP_WIDTH}:${CROP_HEIGHT}:${CROP_X}:${CROP_Y},fps=15,scale=1200:-1:flags=lanczos,split[s0][s1];[s0]palettegen=stats_mode=full[p];[s1][p]paletteuse=dither=bayer:bayer_scale=5" \
        "${out}"
done

echo "Converted ${#videos[@]} video(s) under ${assets_root}/"
