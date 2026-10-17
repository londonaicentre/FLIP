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

# Concatenate the demo segment mp4s, in filename order (01-…06-), into one
# walkthrough video, with an optional blur crossfade between chosen segments.
# The segments come from flip-api/tests/demo_video.py, already encoded at the
# recording size and frame rate, so nothing is cropped or trimmed here.
#
# Usage: assemble-demo-video.sh <segments-dir> <out-file>

set -euo pipefail

# Absolute: the concat list below lives in a temp dir, and ffmpeg resolves its entries from there.
SEGMENTS_DIR="$(cd "$1" && pwd)"
OUT="$2"
FPS=30

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "ffmpeg not found on PATH — install it to assemble the demo video" >&2
    exit 1
fi

shopt -s nullglob
segments=("$SEGMENTS_DIR"/[0-9][0-9]-*.mp4)
if [ "${#segments[@]}" -eq 0 ]; then
    echo "no segment videos found in $SEGMENTS_DIR" >&2
    exit 1
fi
# Journey order == filename order (the glob sorts, and the segments are numbered).

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT
concat_list="$workdir/concat.txt"
: > "$concat_list"

# Blur crossfades between pairs of segment NUMBERS (comma-separated, e.g. "04-05"
# bridges training start → training progress; "none" disables). Matched on the
# filename prefixes, so a skipped segment cannot shift a transition onto the
# wrong cut.
TRANSITIONS="${DEMO_TRANSITIONS:-04-05}"
TRANSITION_SECONDS=0.8

for ((j = 0; j < ${#segments[@]}; j++)); do
    clip_a="${segments[$j]}"
    printf "file '%s'\n" "$clip_a" >> "$concat_list"
    next=$((j + 1))
    [ "$next" -lt "${#segments[@]}" ] || continue
    clip_b="${segments[$next]}"
    pair="$(basename "$clip_a" | cut -c1-2)-$(basename "$clip_b" | cut -c1-2)"
    if [ "$TRANSITIONS" != "none" ] && [[ ",${TRANSITIONS}," == *",${pair},"* ]]; then
        ffmpeg -hide_banner -loglevel error -y -sseof -0.1 -i "$clip_a" -frames:v 1 "$workdir/t${j}a.png"
        ffmpeg -hide_banner -loglevel error -y -i "$clip_b" -frames:v 1 "$workdir/t${j}b.png"
        ffmpeg -hide_banner -loglevel error -y \
            -loop 1 -t "$TRANSITION_SECONDS" -i "$workdir/t${j}a.png" \
            -loop 1 -t "$TRANSITION_SECONDS" -i "$workdir/t${j}b.png" \
            -filter_complex "[0:v][1:v]xfade=transition=hblur:duration=${TRANSITION_SECONDS}:offset=0,fps=${FPS},format=yuv420p" \
            -c:v libx264 -preset veryfast -crf 18 -threads 8 -an "$workdir/trans${j}.mp4"
        printf "file '%s'\n" "$workdir/trans${j}.mp4" >> "$concat_list"
        echo "  blur transition ${pair}"
    fi
done

mkdir -p "$(dirname "$OUT")"
# Re-encode the concat — the generated transition clips make stream-copy
# concatenation unreliable across encoder parameter differences.
ffmpeg -hide_banner -loglevel error -y -f concat -safe 0 -i "$concat_list" \
    -c:v libx264 -preset veryfast -crf 18 -threads 8 -pix_fmt yuv420p -an "$OUT"
echo "assembled ${#segments[@]} segments -> $OUT"
