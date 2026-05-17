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
    # 15 fps + lanczos downscale + per-clip palette keeps file sizes in the
    # 200 KB–1.5 MB envelope used by the existing hand-recorded GIFs.
    ffmpeg -y -hide_banner -loglevel error -i "${mp4}" \
        -vf "fps=15,scale=1200:-1:flags=lanczos,split[s0][s1];[s0]palettegen=stats_mode=full[p];[s1][p]paletteuse=dither=bayer:bayer_scale=5" \
        "${out}"
done

echo "Converted ${#videos[@]} video(s) → ${out_dir}"
