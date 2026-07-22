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

# Cypress keeps recording past the last command and captures its "Default
# blank page" teardown screen at every clip's tail, for a variable duration.
# Detect the real content end instead of trimming a fixed amount: the
# teardown frames are near-uniform (luma entropy < ~0.9 even with the caption
# pill re-injected on them) while every segment's final held app frame
# measures ≥ ~1.9 — cut each clip at its last frame above the threshold.
ENTROPY_THRESHOLD=1.2
TAIL_WINDOW_SECONDS=6
TAIL_SAMPLE_FPS=5
FALLBACK_TRIM_SECONDS=0.8

detect_content_end() {
    # Prints the keep-duration (s) for a clip, or nothing if undetectable.
    local segment="$1" dur_s="$2"
    local start
    start=$(python3 -c "print(max(0, ${dur_s} - ${TAIL_WINDOW_SECONDS}))")
    # Crop to the AUT viewport first — the raw capture also contains the dark
    # runner bezel, whose contrast against the blank page inflates entropy.
    (ffmpeg -hide_banner -ss "$start" -i "$segment" \
        -vf "crop=${CROP_WIDTH}:${CROP_HEIGHT}:${CROP_X}:${CROP_Y},fps=${TAIL_SAMPLE_FPS},scale=480:-1,format=gray,entropy,metadata=mode=print:file=-" \
        -f null - 2>&1 || true) | python3 -c "
import re
import sys

start = float('${start}')
threshold = float('${ENTROPY_THRESHOLD}')
sample_dt = 1.0 / ${TAIL_SAMPLE_FPS}
times, vals = [], []
t = None
for line in sys.stdin:
    m = re.search(r'pts_time:([0-9.]+)', line)
    if m:
        t = float(m.group(1))
        continue
    m = re.search(r'entropy\.entropy\.normal\.Y=([0-9.]+)', line)
    if m and t is not None:
        times.append(start + t)
        vals.append(float(m.group(1)))
for tt, vv in zip(reversed(times), reversed(vals)):
    if vv >= threshold:
        print(f'{tt + sample_dt:.2f}')
        break
"
}

i=0
for segment in "${sorted[@]}"; do
    i=$((i + 1))
    clip="$workdir/$(printf '%02d' "$i").mp4"
    # `ffmpeg -i` with no output exits non-zero by design — tolerate it.
    duration=$( (ffmpeg -i "$segment" 2>&1 || true) | grep -oE "Duration: [0-9:.]+" | cut -d' ' -f2 || true)
    trim_args=()
    if [ -n "$duration" ]; then
        dur_s=$(python3 -c "h, m, s = '${duration}'.split(':'); print(int(h) * 3600 + int(m) * 60 + float(s))")
        keep=$(detect_content_end "$segment" "$dur_s")
        if [ -z "$keep" ]; then
            keep=$(python3 -c "print(max(1.0, ${dur_s} - ${FALLBACK_TRIM_SECONDS}))")
        fi
        trim_args=(-t "$keep")
        echo "  cropping $(basename "$segment") (keeping ${keep}s of ${dur_s}s)"
    else
        echo "  cropping $(basename "$segment") (duration unknown — no trim)"
    fi
    ffmpeg -hide_banner -loglevel error -y -i "$segment" "${trim_args[@]}" \
        -vf "crop=${CROP_WIDTH}:${CROP_HEIGHT}:${CROP_X}:${CROP_Y},fps=${FPS},format=yuv420p" \
        -c:v libx264 -preset veryfast -crf 18 -an "$clip"
    echo "file '$clip'" >> "$concat_list"
done

mkdir -p "$(dirname "$OUT")"
ffmpeg -hide_banner -loglevel error -y -f concat -safe 0 -i "$concat_list" -c copy "$OUT"
echo "assembled ${#sorted[@]} segments -> $OUT"
