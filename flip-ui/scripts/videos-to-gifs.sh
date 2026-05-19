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

# Convert each Cypress-recorded mp4 under test/cypress/videos/docs/admin/ into
# a GIF in-place under docs/source/assets/admin/. Spec filenames map 1:1 to GIF
# basenames (reset-mfa.spec.ts.mp4 → reset-mfa.gif).

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ui_dir="$(cd "${script_dir}/.." && pwd)"
repo_root="$(cd "${ui_dir}/.." && pwd)"

videos_root="${ui_dir}/test/cypress/videos"
out_dir="${repo_root}/docs/source/assets/admin"

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

# Cypress writes each video to {videosFolder}/{specRelativePath}.mp4, but the
# exact prefix varies by version and common-root inference — find any mp4 under
# the videos root and key off the filename. Demo specs are named to match the
# target GIF (reset-mfa.spec.ts → reset-mfa.gif), so the basename carries
# everything we need.
mapfile -t videos < <(find "${videos_root}" -type f -name "*.mp4" | sort)
if (( ${#videos[@]} == 0 )); then
    echo "No mp4 files under ${videos_root}; nothing to convert." >&2
    exit 0
fi

mkdir -p "${out_dir}"

for mp4 in "${videos[@]}"; do
    base="$(basename "${mp4}")"
    # reset-mfa.spec.ts.mp4 → reset-mfa
    name="${base%.spec.ts.mp4}"
    if [[ "${name}" == "${base}" ]]; then
        # Fallback for unexpected naming — strip a single trailing extension.
        name="${base%.mp4}"
    fi
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

echo "Converted ${#videos[@]} video(s) → ${out_dir}"
