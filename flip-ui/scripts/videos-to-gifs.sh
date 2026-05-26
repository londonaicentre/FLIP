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

# Convert each Cypress-recorded demo mp4 into a GIF under docs/source/assets/.
# Demo specs live at test/cypress/docs/<category>/<name>.spec.ts and map 1:1 to
# <name>.gif under docs/source/assets/<category>/ — the spec's folder selects
# the asset subdirectory, the basename selects the GIF:
#   test/cypress/docs/admin/reset-mfa.spec.ts    -> docs/source/assets/admin/reset-mfa.gif
#   test/cypress/docs/flip/create-model.spec.ts  -> docs/source/assets/flip/create-model.gif

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ui_dir="$(cd "${script_dir}/.." && pwd)"
repo_root="$(cd "${ui_dir}/.." && pwd)"

specs_root="${ui_dir}/test/cypress/docs"
videos_root="${ui_dir}/test/cypress/videos"
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

# Drive off the demo spec files, not the recorded videos: the spec path is
# stable and authoritative for both the GIF basename and its category subdir,
# whereas Cypress's video-path prefix varies by version and common-root
# inference. Each spec's video is then located by its unique filename.
mapfile -t specs < <(find "${specs_root}" -type f -name "*.spec.ts" | sort)
if (( ${#specs[@]} == 0 )); then
    echo "No demo specs under ${specs_root}; nothing to convert." >&2
    exit 0
fi

converted=0
for spec in "${specs[@]}"; do
    # test/cypress/docs/flip/create-model.spec.ts -> category=flip, name=create-model
    category="$(basename "$(dirname "${spec}")")"
    name="$(basename "${spec}" .spec.ts)"

    # Scope the search to the spec's category subdir so a same-named spec in a
    # different category can't be picked up by mistake, and bail on >1 matches
    # rather than silently pick a stale recording (e.g. left over from a run
    # against a different Cypress version with a different video-path prefix).
    mapfile -t mp4_matches < <(find "${videos_root}" -type f -path "*/${category}/${name}.spec.ts.mp4" | sort)
    if (( ${#mp4_matches[@]} == 0 )); then
        echo "No video for ${spec}; skipping." >&2
        continue
    fi
    if (( ${#mp4_matches[@]} > 1 )); then
        echo "Multiple videos for ${spec} — clean ${videos_root} and re-record:" >&2
        printf '  %s\n' "${mp4_matches[@]}" >&2
        exit 1
    fi
    mp4="${mp4_matches[0]}"

    out_dir="${assets_root}/${category}"
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
    converted=$(( converted + 1 ))
done

echo "Converted ${converted} video(s) → ${assets_root}/{admin,flip}"
