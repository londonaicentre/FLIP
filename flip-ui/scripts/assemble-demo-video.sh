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

# Crop each demo segment mp4 down to the AUT viewport and concatenate them,
# in filename order (01-…06-), into a single walkthrough video. Companion to
# scripts/videos-to-gifs.sh: the crop window below MUST stay in lockstep with
# the constants there and with cypress.demo.config.ts (1280x800 viewport
# rendered 1:1 inside a 1920x1200 Chrome window).
#
# Usage: assemble-demo-video.sh [videos-dir] [out-file] [scale]
#   scale (default 1) must match the DEMO_VIDEO_SCALE the segments were
#   recorded with — the browser framebuffer (and therefore the crop window)
#   is scale× the DIP geometry.

set -euo pipefail

VIDEOS_DIR="${1:-test/cypress/demo/videos}"
OUT="${2:-test/cypress/demo/out/flip-demo.mp4}"
SCALE="${3:-${DEMO_VIDEO_SCALE:-1}}"

# Keep identical to CROP_* in scripts/videos-to-gifs.sh (at scale 1).
CROP_WIDTH=$((1280 * SCALE))
CROP_HEIGHT=$((800 * SCALE))
CROP_X=$((540 * SCALE))
CROP_Y=$((80 * SCALE))
FPS=30

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "ffmpeg not found on PATH — install it to assemble the demo video" >&2
    exit 1
fi

shopt -s nullglob
segments=("$VIDEOS_DIR"/*.mp4)
if [ "${#segments[@]}" -eq 0 ]; then
    echo "no segment videos found in $VIDEOS_DIR" >&2
    exit 1
fi
# Journey order == filename order (the segments are numbered 01-…06-).
IFS=$'\n' sorted=($(printf '%s\n' "${segments[@]}" | sort))
unset IFS

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT
concat_list="$workdir/concat.txt"
: > "$concat_list"

i=0
for segment in "${sorted[@]}"; do
    i=$((i + 1))
    clip="$workdir/$(printf '%02d' "$i").mp4"
    echo "  cropping $(basename "$segment")"
    ffmpeg -hide_banner -loglevel error -y -i "$segment" \
        -vf "crop=${CROP_WIDTH}:${CROP_HEIGHT}:${CROP_X}:${CROP_Y},fps=${FPS},format=yuv420p" \
        -c:v libx264 -preset veryfast -crf 18 -an "$clip"
    echo "file '$clip'" >> "$concat_list"
done

mkdir -p "$(dirname "$OUT")"
ffmpeg -hide_banner -loglevel error -y -f concat -safe 0 -i "$concat_list" -c copy "$OUT"
echo "assembled ${#sorted[@]} segments -> $OUT"
