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

# Cypress records beyond the spec on BOTH ends of every clip: its "Default
# blank page" (cy logo) shows before the first cy.visit renders and again for
# a variable 0-3s after the last command. Detect the real content span
# instead of trimming fixed amounts: blank/teardown frames are near-uniform
# (luma entropy < ~0.9 even with the caption pill re-injected on them) while
# real app frames measure ≥ ~1.9 — keep from the first frame above the
# threshold to the last. This also drops the white load-flash at each
# segment's head, so the concat cuts land straight on rendered pages.
ENTROPY_THRESHOLD=1.2
SAMPLE_FPS=5
FALLBACK_TRIM_SECONDS=0.8
# Clips (1-based indices, comma-separated) whose head should start after the
# LAST leading blank run instead of the first contentful frame — used for the
# stealth segments, where a briefly-visible sign-in precedes the hidden
# stretch before the dashboard reveal.
HEAD_AFTER_BLANK="${DEMO_HEAD_AFTER_BLANK:-5,6}"
# A killed Cypress run leaves a truncated mp4 behind, which this script would
# otherwise stitch in without comment — a whole beat silently missing from the
# cut. Every scripted segment holds for well over this, so flag the short ones.
MIN_SEGMENT_SECONDS="${DEMO_MIN_SEGMENT_SECONDS:-10}"

detect_content_span() {
    # Prints "start end" (s) for a clip's contentful span, or nothing.
    local segment="$1" head_mode="${2:-first}"
    # Crop to the AUT viewport first — the raw capture also contains the dark
    # runner bezel, whose contrast against the blank page inflates entropy.
    (ffmpeg -hide_banner -i "$segment" \
        -vf "crop=${CROP_WIDTH}:${CROP_HEIGHT}:${CROP_X}:${CROP_Y},fps=${SAMPLE_FPS},scale=480:-1,format=gray,entropy,metadata=mode=print:file=-" \
        -f null - 2>&1 || true) | python3 -c "
import re
import sys

threshold = float('${ENTROPY_THRESHOLD}')
sample_dt = 1.0 / ${SAMPLE_FPS}
times, vals = [], []
t = None
for line in sys.stdin:
    m = re.search(r'pts_time:([0-9.]+)', line)
    if m:
        t = float(m.group(1))
        continue
    m = re.search(r'entropy\.entropy\.normal\.Y=([0-9.]+)', line)
    if m and t is not None:
        times.append(t)
        vals.append(float(m.group(1)))
# Cut exactly at the sampled contentful frames: padding by a sample
# interval on either side re-includes blank frames (at ${SAMPLE_FPS}fps the
# cost is ≤1/${SAMPLE_FPS}s of real content, imperceptible against the
# multi-second holds every segment starts and ends on).
del sample_dt
head_mode = '${head_mode}'
content_idx = [i for i, vv in enumerate(vals) if vv >= threshold]
if content_idx:
    start = times[content_idx[0]]
    if head_mode == 'afterblank':
        # Start after the LAST blank sample in the first 8s that still has
        # content after it (skips a visible sign-in + hidden stretch).
        blanks = [i for i, (tt, vv) in enumerate(zip(times, vals)) if tt <= 8.0 and vv < threshold]
        for i in reversed(blanks):
            later = [j for j in content_idx if j > i]
            if later:
                start = times[later[0]]
                break
    print(f'{start:.2f} {times[content_idx[-1]]:.2f}')
"
}

i=0
for segment in "${sorted[@]}"; do
    i=$((i + 1))
    clip="$workdir/$(printf '%02d' "$i").mp4"
    head_mode="first"
    if [[ ",${HEAD_AFTER_BLANK}," == *",${i},"* ]]; then
        head_mode="afterblank"
    fi
    span=$(detect_content_span "$segment" "$head_mode")
    trim_args=()
    keep=""
    if [ -n "$span" ]; then
        span_start=${span% *}
        span_end=${span#* }
        keep=$(python3 -c "print(f'{${span_end} - ${span_start}:.2f}')")
        # -ss placed AFTER -i: frame-accurate output seeking, so no blank
        # keyframe leaks back in at the head.
        trim_args=(-ss "$span_start" -t "$keep")
        echo "  cropping $(basename "$segment") (content ${span_start}s → ${span_end}s)"
    else
        duration=$( (ffmpeg -i "$segment" 2>&1 || true) | grep -oE "Duration: [0-9:.]+" | cut -d' ' -f2 || true)
        if [ -n "$duration" ]; then
            keep=$(python3 -c "
h, m, s = '${duration}'.split(':')
print(f'{max(1.0, int(h) * 3600 + int(m) * 60 + float(s) - ${FALLBACK_TRIM_SECONDS}):.2f}')
")
            trim_args=(-t "$keep")
        fi
        echo "  cropping $(basename "$segment") (span undetected — fallback trim)"
    fi
    if [ -n "$keep" ] && python3 -c "import sys; sys.exit(0 if ${keep} < ${MIN_SEGMENT_SECONDS} else 1)"; then
        echo "  ⚠️  $(basename "$segment") keeps only ${keep}s — likely a truncated take; re-record it" >&2
    fi
    ffmpeg -hide_banner -loglevel error -y -i "$segment" "${trim_args[@]}" \
        -vf "crop=${CROP_WIDTH}:${CROP_HEIGHT}:${CROP_X}:${CROP_Y},fps=${FPS},format=yuv420p" \
        -c:v libx264 -preset veryfast -crf 18 -an "$clip"
done

# Optional blur crossfades between selected clip pairs (1-based indices,
# comma-separated, e.g. "4-5" bridges training-start → training-progress;
# "none" disables). Each transition is a short xfade clip built from the
# boundary frames and spliced into the concat.
TRANSITIONS="${DEMO_TRANSITIONS:-4-5}"
TRANSITION_SECONDS=0.8

: > "$concat_list"
count=$i
for ((j = 1; j <= count; j++)); do
    printf "file '%s'\n" "$workdir/$(printf '%02d' "$j").mp4" >> "$concat_list"
    next=$((j + 1))
    if [ "$TRANSITIONS" != "none" ] && [ "$next" -le "$count" ] \
        && [[ ",${TRANSITIONS}," == *",${j}-${next},"* ]]; then
        clip_a="$workdir/$(printf '%02d' "$j").mp4"
        clip_b="$workdir/$(printf '%02d' "$next").mp4"
        ffmpeg -hide_banner -loglevel error -y -sseof -0.1 -i "$clip_a" -frames:v 1 "$workdir/t${j}a.png"
        ffmpeg -hide_banner -loglevel error -y -i "$clip_b" -frames:v 1 "$workdir/t${j}b.png"
        ffmpeg -hide_banner -loglevel error -y \
            -loop 1 -t "$TRANSITION_SECONDS" -i "$workdir/t${j}a.png" \
            -loop 1 -t "$TRANSITION_SECONDS" -i "$workdir/t${j}b.png" \
            -filter_complex "[0:v][1:v]xfade=transition=hblur:duration=${TRANSITION_SECONDS}:offset=0,fps=${FPS},format=yuv420p" \
            -c:v libx264 -preset veryfast -crf 18 -an "$workdir/trans${j}.mp4"
        printf "file '%s'\n" "$workdir/trans${j}.mp4" >> "$concat_list"
        echo "  blur transition between clips ${j} and ${next}"
    fi
done

mkdir -p "$(dirname "$OUT")"
# Re-encode the concat — the generated transition clips make stream-copy
# concatenation unreliable across encoder parameter differences.
ffmpeg -hide_banner -loglevel error -y -f concat -safe 0 -i "$concat_list" \
    -c:v libx264 -preset veryfast -crf 18 -pix_fmt yuv420p -an "$OUT"
echo "assembled ${#sorted[@]} segments -> $OUT"
