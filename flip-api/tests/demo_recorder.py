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
"""Playwright recording kit for the demo video: one ``Take`` per segment.

A take is a fresh browser context whose page is recorded through Chromium's screencast. Every frame
is written to disk with its timestamp and encoded to h264 by ffmpeg here, rather than through
Playwright's built-in recorder (VP8 at a low fixed bitrate, visibly soft on movement at 4K).

Recording starts and stops when the segment says so, so sign-ins and page loads that should not be
on camera happen before ``start()``. The screencast emits no frames while the page is still; each
frame is held until the next one arrives, so a still page keeps its real length in the video.
"""

from __future__ import annotations

import html
import shutil
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from playwright.sync_api import Browser, Locator, Page

from tests.e2e_smoke import _log

# The design viewport. Chromium renders it at `scale`× device pixels: 3 gives 3840x2400.
WIDTH, HEIGHT = 1280, 800
FPS = 30
ENCODE_THREADS = 8

CAPTION_HTML = (
    '<div style="position:fixed;left:50%;bottom:26px;transform:translateX(-50%);max-width:76%;'
    "padding:10px 24px;border-radius:9999px;background:rgba(17,24,39,.85);color:#f9fafb;"
    "font:17px/1.45 -apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;text-align:center;"
    'box-shadow:0 4px 14px rgba(0,0,0,.35)">{text}</div>'
)

# A headless browser draws no pointer, so the page gets one: the same arrow as the docs-GIF cursor
# (flip-ui/test/cypress/docs/support/demoCursor.ts), driven by the real mouse events Playwright
# sends, with a purple pulse on every click. The position survives navigations via sessionStorage.
CURSOR_JS = r"""
(() => {
  if (window.top !== window) return;
  const ID = "demo-cursor";
  const SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 28 28" width="28" height="28">'
    + '<path d="M5 3 L23 13 L14 14 L10 23 Z" fill="#111827" stroke="#ffffff" stroke-width="1.5"'
    + ' stroke-linejoin="round"/></svg>';
  const CSS = `#${ID}{position:fixed;top:0;left:0;width:28px;height:28px;z-index:2147483647;pointer-events:none;
      transition:transform 380ms ease;will-change:transform;filter:drop-shadow(0 1px 1px rgba(0,0,0,.35))}
    #${ID}::after{content:"";position:absolute;width:26px;height:26px;top:-7px;left:-7px;border-radius:50%;
      border:3px solid rgba(124,58,237,.95);background:rgba(124,58,237,.28);opacity:0}
    #${ID}.clicking::after{animation:demo-cursor-pulse 600ms ease-out}
    @keyframes demo-cursor-pulse{0%{opacity:1;transform:scale(.4)}100%{opacity:0;transform:scale(1.6)}}`;
  let pos = JSON.parse(sessionStorage.getItem(ID) || '{"x":640,"y":400}');
  const place = (el) => { el.style.transform = `translate3d(${pos.x - 6}px, ${pos.y - 6}px, 0)`; };
  const mount = () => {
    if (document.getElementById(ID)) return;
    const style = document.createElement("style");
    style.textContent = CSS;
    document.head.appendChild(style);
    const el = document.createElement("div");
    el.id = ID;
    el.innerHTML = SVG;
    place(el);
    document.documentElement.appendChild(el);
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount); else mount();
  window.addEventListener("mousemove", (e) => {
    pos = { x: e.clientX, y: e.clientY };
    sessionStorage.setItem(ID, JSON.stringify(pos));
    const el = document.getElementById(ID);
    if (el) place(el);
  }, true);
  window.addEventListener("mousedown", () => {
    const el = document.getElementById(ID);
    if (!el) return;
    el.classList.remove("clicking");
    void el.offsetWidth;
    el.classList.add("clicking");
  }, true);
})();
"""

# Force-load every declared font face, so the first recorded frame is not the fallback font.
# `document.fonts.ready` alone resolves before an SPA has rendered any text.
LOAD_FONTS_JS = "() => Promise.all(Array.from(document.fonts).map((face) => face.load().catch(() => null)))"

GLIDE_MS = 450  # the cursor's CSS transition, plus a beat


@dataclass
class FrameSink:
    """Writes each screencast frame to disk with the time it is meant to be shown."""

    directory: Path
    frames: list[dict[str, Any]] = field(default_factory=list)

    def on_frame(self, frame: Any) -> None:
        name = f"{len(self.frames):06d}.jpg"
        (self.directory / name).write_bytes(frame["data"])
        self.frames.append({"file": name, "ts": frame["timestamp"], "arrival": time.time() * 1000})

    def write_concat(self, stop_ms: float) -> Path:
        """Write an ffmpeg concat list holding each frame until the next one arrives.

        Frame timestamps are on Chromium's clock and ``stop_ms`` on ours, and the two can disagree by
        seconds, so the last frame's hold is measured on our clock alone (its arrival to the stop).

        Args:
            stop_ms (float): When recording stopped, in epoch milliseconds.

        Returns:
            Path: The concat list, beside the frames.
        """
        lines = []
        for index, frame in enumerate(self.frames):
            if index + 1 < len(self.frames):
                hold = self.frames[index + 1]["ts"] - frame["ts"]
            else:
                hold = stop_ms - frame["arrival"]
            lines += [f"file '{frame['file']}'", f"duration {max(hold, 1) / 1000:.4f}"]
        # The concat demuxer ignores the last entry's duration unless the file is repeated.
        lines.append(f"file '{self.frames[-1]['file']}'")
        path = self.directory / "concat.txt"
        path.write_text("\n".join(lines) + "\n")
        return path


class Take:
    """One recorded segment: a page, its captions and pointer, and the frames it produced."""

    def __init__(self, page: Page, frames_dir: Path, scale: int) -> None:
        self.page = page
        self.scale = scale
        self.sink = FrameSink(frames_dir)
        self._caption: Any = None
        self._recording = False
        self._stop_ms = 0.0

    def start(self) -> None:
        """Wait for the current page's fonts, then start recording."""
        self.page.evaluate(LOAD_FONTS_JS)
        self.page.screencast.start(
            on_frame=self.sink.on_frame, size={"width": WIDTH * self.scale, "height": HEIGHT * self.scale}
        )
        self._recording = True

    def stop(self) -> None:
        if self._recording:
            self._stop_ms = time.time() * 1000
            self.page.screencast.stop()
            self._recording = False

    def caption(self, text: str, hold_ms: int = 900) -> None:
        """Replace the subtitle banner (an empty text clears it), then hold.

        Screencast overlays survive navigations, so a caption stays up across page loads.
        """
        if self._caption is not None:
            # Playwright 1.63's sync API hands back the overlay's dispose() as a bare coroutine
            # rather than running it, so the old caption would stay on screen; drive it here.
            result = self._caption.dispose()
            if hasattr(result, "__await__"):
                self.page.screencast._sync(result)
            self._caption = None
        if text:
            self._caption = self.page.screencast.show_overlay(CAPTION_HTML.format(text=html.escape(text)))
        self.pause(hold_ms)

    def pause(self, ms: int = 600) -> None:
        if ms:
            self.page.wait_for_timeout(ms)

    def glide(self, locator: Locator, fx: float = 0.5, fy: float = 0.5) -> None:
        """Move the pointer to a point on an element, the way a user would."""
        locator.wait_for(state="visible")
        locator.scroll_into_view_if_needed()
        box = locator.bounding_box()
        if box is None:
            raise RuntimeError(f"{locator} has no bounding box to point at")
        self.page.mouse.move(box["x"] + box["width"] * fx, box["y"] + box["height"] * fy)
        self.pause(GLIDE_MS)

    def click(self, locator: Locator) -> None:
        self.glide(locator)
        locator.click()

    def type(self, locator: Locator, text: str, delay_ms: int = 60) -> None:
        self.click(locator)
        locator.press_sequentially(text, delay=delay_ms)

    def hover(self, locator: Locator, fx: float, fy: float, dwell_ms: int) -> None:
        self.glide(locator, fx, fy)
        self.pause(dwell_ms)

    def scroll_to(self, locator: Locator, pause_ms: int = 900) -> None:
        """Smooth-scroll an element into the middle of the viewport."""
        locator.evaluate("(el) => el.scrollIntoView({behavior: 'smooth', block: 'center'})")
        self.pause(pause_ms)

    def encode(self, out: Path) -> Path:
        """Encode the recorded frames to an h264 mp4 at exactly the recording size."""
        if not self.sink.frames:
            raise RuntimeError("the take recorded no frames")
        concat = self.sink.write_concat(self._stop_ms)
        width, height = WIDTH * self.scale, HEIGHT * self.scale
        fit = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
        out.parent.mkdir(parents=True, exist_ok=True)
        # At 4K, x264's defaults (a frame per thread at 1.5 threads per core, a 50-frame lookahead on
        # "slow") reached 5.5 GB on a 32-core dev box already short of memory; capped, about 2 GB.
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", concat.name,
             "-vf", f"{fit},fps={FPS},format=yuv420p", "-c:v", "libx264", "-preset", "slow", "-crf", "18",
             "-threads", str(ENCODE_THREADS), "-rc-lookahead", "20", "-movflags", "+faststart", str(out.resolve())],
            cwd=self.sink.directory,
            check=True,
        )  # fmt: skip
        return out


@contextmanager
def take(browser: Browser, out: Path, base_url: str, scale: int) -> Iterator[Take]:
    """Record one segment to ``out``, in its own browser context.

    On failure the page is screenshotted beside ``out`` and no video is written; on success the
    frames are encoded and deleted.

    Args:
        browser (Browser): Chromium launched with ``--force-device-scale-factor=<scale>``.
        out (Path): The segment mp4 to write.
        base_url (str): What relative ``page.goto`` paths resolve against.
        scale (int): Device pixels per CSS pixel.
    """
    frames_dir = out.with_suffix(".frames")
    shutil.rmtree(frames_dir, ignore_errors=True)
    frames_dir.mkdir(parents=True)
    out.unlink(missing_ok=True)
    context = browser.new_context(
        viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=scale, base_url=base_url
    )
    context.add_init_script(CURSOR_JS)
    page = context.new_page()
    page.set_default_timeout(60000)
    recording = Take(page, frames_dir, scale)
    try:
        yield recording
    except BaseException:
        with suppress(Exception):
            page.screenshot(path=str(out.with_suffix(".failure.png")))
            _log(f"  📸 page at the failure: {out.with_suffix('.failure.png')}")
        raise
    finally:
        recording.stop()
        context.close()
    recording.encode(out)
    shutil.rmtree(frames_dir)
    _log(f"  ✅ {out.name}")
