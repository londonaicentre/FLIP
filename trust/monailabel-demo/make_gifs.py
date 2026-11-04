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
"""Cut the documentation GIFs out of a demo recording: one per chapter record_demo.py marked.

Writes ``<out>/gifs/monailabel/monailabel-<chapter>.gif`` for every chapter in ``<out>/result.json``, with the
same ffmpeg chain as the Cypress GIFs (flip-ui/scripts/videos-to-gifs.sh: 15 fps, 1200 px wide, one palette
per clip, Bayer dither), so they sit beside them in the docs looking alike. These are the GIFs listed in
docs/scripts/live-recorded-gifs.txt; copy them into docs/source/assets/generated/gifs/ to preview or publish.
"""

import json
import os
import subprocess
from pathlib import Path

OUT = Path(os.environ.get("OUT_DIR", "/out"))
FPS = os.environ.get("GIF_FPS", "15")
WIDTH = os.environ.get("GIF_WIDTH", "1200")
PALETTE = "split[s0][s1];[s0]palettegen=stats_mode=full[p];[s1][p]paletteuse=dither=bayer:bayer_scale=5"


def main() -> None:
    chapters = json.loads((OUT / "result.json").read_text())["chapters"]
    target_dir = OUT / "gifs" / "monailabel"
    target_dir.mkdir(parents=True, exist_ok=True)
    for chapter in chapters:
        gif = target_dir / f"monailabel-{chapter['name']}.gif"
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-ss", str(chapter["start"]), "-to", str(chapter["end"]), "-i", str(OUT / "monailabel-demo.mp4"),
             "-vf", f"fps={FPS},scale={WIDTH}:-1:flags=lanczos,{PALETTE}", str(gif)],
            check=True,
        )  # fmt: skip
        print(f"{gif.name}: {chapter['end'] - chapter['start']:.1f} s, {gif.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
