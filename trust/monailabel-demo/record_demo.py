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
"""Record the MONAI Label demo: a spleen built from nothing with SAM clicks, then exported for FL.

Drives a live trust (XNAT with the OHIF Viewer plugin, a registered MONAI Label server, export_mask
configured) the way a clinician would: a new empty segment, sam_2d prompted with a positive click on
the spleen and negative (Ctrl) clicks where it leaks, on the spleen's largest slice. The clicks are
chosen off camera by choose_clicks.py (the fewest reaching Dice > 0.95). The recording fails unless
export_mask leaves the NIfTI beside the saved DICOM-SEG, so a finished recording is also an
end-to-end check. Run it with `make demo-video` in this directory (see README.md).

Frames come from Playwright's screencast and are encoded here (h264 CRF 18): its built-in recorder is
VP8 at 1 Mbit/s, and a still page produces no frames at all, so each frame is held until the next.
"""

import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import SimpleITK as sitk
from choose_clicks import Click, choose
from playwright.sync_api import Locator, Page, sync_playwright

XNAT = os.environ.get("XNAT_URL", "http://127.0.0.1:8105").rstrip("/")
USER = os.environ.get("XNAT_USER", "admin")
PASSWORD = os.environ["XNAT_PASSWORD"]
EXPERIMENT = os.environ["XNAT_EXPERIMENT"]
SAM_MODEL = os.environ.get("SAM_MODEL", "sam_2d")
TARGET_DICE = float(os.environ.get("TARGET_DICE", "0.95"))
MONAI_LABEL = os.environ.get("MONAI_LABEL_URL", "http://127.0.0.1:8030").rstrip("/")
SCALE = int(os.environ.get("DEMO_VIDEO_SCALE", "2"))
OUT = Path(os.environ.get("OUT_DIR", "/out"))
CONVERT_TIMEOUT_S = int(os.environ.get("EXPORT_MASK_TIMEOUT", "300"))
W, H = 1280, 800

# Captions and a visible cursor, injected into every page the demo visits. Playwright's own
# show_actions() prints the raw selector of each action, which is not something to put on camera.
OVERLAY_JS = """
(() => {
  const ready = () => {
    if (document.getElementById('demo-caption')) return;
    const caption = document.createElement('div');
    caption.id = 'demo-caption';
    caption.style.cssText = 'position:fixed;left:50%;bottom:26px;transform:translateX(-50%);max-width:76%;' +
      'padding:10px 24px;border-radius:9999px;background:rgba(17,24,39,.94);color:#f9fafb;z-index:2147483647;' +
      "font:17px/1.45 -apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;text-align:center;" +
      'box-shadow:0 4px 14px rgba(0,0,0,.35);pointer-events:none;display:none';
    const cursor = document.createElement('div');
    cursor.id = 'demo-cursor';
    cursor.style.cssText = 'position:fixed;width:22px;height:22px;margin:-11px 0 0 -11px;border-radius:50%;' +
      'background:rgba(250,204,21,.55);border:2px solid rgba(250,204,21,.95);z-index:2147483647;' +
      'pointer-events:none;left:-40px;top:-40px;transition:transform .12s';
    document.body.append(caption, cursor);
    document.addEventListener('mousemove', e => {
      cursor.style.left = e.clientX + 'px';
      cursor.style.top = e.clientY + 'px';
    }, true);
    document.addEventListener('mousedown', () => { cursor.style.transform = 'scale(.7)'; }, true);
    document.addEventListener('mouseup', () => { cursor.style.transform = ''; }, true);
    window.__demoCaption = text => { caption.textContent = text; caption.style.display = text ? 'block' : 'none'; };
  };
  document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', ready) : ready();
})();
"""


# Patient-space point (mm) -> page coordinates on the displayed slice, through cornerstone's own plane
# geometry, so it holds whatever the scan's orientation. Also returns how far the point is from that
# plane, which the recorder checks before every click.
LPS_TO_PAGE = """([x, y, z]) => {
  const cs = window.cornerstone, el = cs.getEnabledElements()[0].element;
  const plane = cs.metaData.get('imagePlaneModule', cs.getImage(el).imageId);
  const p = plane.imagePositionPatient, r = plane.rowCosines, c = plane.columnCosines;
  const d = [x - p[0], y - p[1], z - p[2]], dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const normal = [r[1] * c[2] - r[2] * c[1], r[2] * c[0] - r[0] * c[2], r[0] * c[1] - r[1] * c[0]];
  const canvas = cs.pixelToCanvas(el, {x: dot(d, r) / plane.columnPixelSpacing, y: dot(d, c) / plane.rowPixelSpacing});
  const box = el.getBoundingClientRect();
  return {x: box.left + canvas.x, y: box.top + canvas.y, offPlane: Math.abs(dot(d, normal))};
}"""


class Frames:
    """Screencast frames written to disk with their timestamps, encoded at the end."""

    def __init__(self, directory: Path) -> None:
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)
        self.frames: list[tuple[str, float]] = []

    def on_frame(self, frame: dict) -> None:
        name = f"{len(self.frames):06d}.jpg"
        (self.dir / name).write_bytes(frame["data"])
        self.frames.append((name, frame["timestamp"]))

    def encode(self, stopped_at_ms: float, target: Path) -> None:
        lines = []
        for i, (name, ts) in enumerate(self.frames):
            end = self.frames[i + 1][1] if i + 1 < len(self.frames) else stopped_at_ms
            lines += [f"file '{name}'", f"duration {max(end - ts, 1) / 1000:.4f}"]
        lines.append(f"file '{self.frames[-1][0]}'")
        (self.dir / "list.txt").write_text("\n".join(lines) + "\n")
        w, h = W * SCALE, H * SCALE
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt",
             "-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps=30,"
             "format=yuv420p", "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-movflags", "+faststart",
             str(target.resolve())],
            cwd=self.dir, check=True,
        )  # fmt: skip


class Demo:
    def __init__(self, page: Page) -> None:
        self.page = page
        self.marks: list[tuple[str, float]] = []  # (chapter, wall-clock ms) - cut into the docs GIFs

    def chapter(self, name: str) -> None:
        """Start a chapter: make_gifs.py cuts one GIF per chapter, from here to the next one (or the end)."""
        self.marks.append((name, time.time() * 1000))

    def caption(self, text: str, hold_ms: int = 0) -> None:
        self.page.evaluate("t => window.__demoCaption && window.__demoCaption(t)", text)
        if hold_ms:
            self.page.wait_for_timeout(hold_ms)

    def tidy(self) -> None:
        """Drop XNAT's "N sessions open from M addresses" nag: it counts the box's other users, not this demo."""
        self.page.evaluate("() => document.getElementById('warning_bar')?.remove()")

    def click(self, target: Locator, pause_ms: int = 350) -> None:
        """Glide the cursor to the target, then click it, so a viewer can follow the hand."""
        target.wait_for(state="visible")
        box = target.bounding_box()
        self.page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=18)
        self.page.wait_for_timeout(pause_ms)
        target.click()

    def type(self, target: Locator, text: str) -> None:
        self.click(target)
        target.press_sequentially(text, delay=55)

    def prompt(self, click: Click, lps: tuple[float, float, float]) -> None:
        """Click a SAM prompt onto the CT (Ctrl+click for a negative one) and wait for the model's answer."""
        at = self.page.evaluate(LPS_TO_PAGE, list(lps))
        assert at["offPlane"] < 1.0, f"prompt is {at['offPlane']:.1f} mm off the displayed slice"
        self.page.mouse.move(at["x"], at["y"], steps=18)
        self.page.wait_for_timeout(400)
        with self.page.expect_response(lambda r: "/infer/" in r.url and r.request.method == "POST"):
            if not click.positive:
                self.page.keyboard.down("Control")
            self.page.mouse.click(at["x"], at["y"])
            if not click.positive:
                self.page.keyboard.up("Control")
        self.page.wait_for_timeout(1500)  # the mask paints after the response


def xnat_api(pw):
    return pw.request.new_context(
        base_url=XNAT, http_credentials={"username": USER, "password": PASSWORD, "send": "always"}
    )


def seg_files(api, experiment: str) -> dict[str, list[str]]:
    """Every ROI collection on the session -> the files in its SEG resource."""
    url = f"/data/experiments/{experiment}/assessors?format=json"
    assessors = [a["ID"] for a in api.get(url).json()["ResultSet"]["Result"] if a["xsiType"] == "icr:roiCollectionData"]
    return {
        a: [f["Name"] for f in api.get(f"/data/experiments/{experiment}/assessors/{a}/resources/SEG/files?format=json")
            .json()["ResultSet"]["Result"]]
        for a in assessors
    }  # fmt: skip


def plan(api, experiment: str) -> dict:
    """Off camera: the slice to annotate, the clicks to make there, and each click in patient space.

    Asks MONAI Label for segmentation_spleen's mask (where the spleen is), picks its largest slice,
    and searches sam_2d prompts on it against the session's ground-truth label when it has one
    (dcm2niix's NIFTI resource carries label_*.nii.gz on the spleen tutorial's data).
    """
    fields = api.get(f"/data/experiments/{experiment}?format=json").json()["items"][0]["data_fields"]
    scans = api.get(f"/data/experiments/{experiment}/scans?format=json").json()["ResultSet"]["Result"]
    (scan,) = [s["ID"] for s in scans]
    image = f"{fields['project']}/{fields['subject_ID']}/{experiment}/{scan}"
    workdir = Path(tempfile.mkdtemp())

    def infer(model: str, positive=(), negative=(), z: int | None = None) -> sitk.Image:
        params = {"result_extension": ".nii.gz", "result_dtype": "uint8", "result_compress": True,
                  "foreground": [[*p, z] for p in positive], "background": [[*p, z] for p in negative]}  # fmt: skip
        response = api.post(f"{MONAI_LABEL}/infer/{model}", params={"image": image, "output": "image"},
                            multipart={"params": json.dumps(params)}, timeout=300_000)  # fmt: skip
        assert response.ok, f"MONAI Label {model} {response.status}: {response.text()[:200]}"
        path = workdir / f"{model}.nii.gz"
        path.write_bytes(response.body())
        return sitk.ReadImage(str(path))

    oracle_image = infer("segmentation_spleen")
    oracle = sitk.GetArrayFromImage(oracle_image) > 0  # (z, row, column)
    assert oracle.any(), f"segmentation_spleen found no spleen on {experiment}"
    z = int(oracle.sum(axis=(1, 2)).argmax())

    files = api.get(f"/data/experiments/{experiment}/scans/{scan}/resources/NIFTI/files?format=json").json()
    labels = [f["Name"] for f in files["ResultSet"]["Result"] if f["Name"].startswith("label_")]
    if labels:
        path = workdir / labels[0]
        path.write_bytes(
            api.get(f"/data/experiments/{experiment}/scans/{scan}/resources/NIFTI/files/{labels[0]}").body()
        )
        truth = sitk.Resample(sitk.ReadImage(str(path)), oracle_image, sitk.Transform(), sitk.sitkNearestNeighbor, 0)
        reference, against = sitk.GetArrayFromImage(truth)[z] > 0, "the expert's label"
    else:
        reference, against = oracle[z], "segmentation_spleen"

    clicks = choose(lambda pos, neg: sitk.GetArrayFromImage(infer(SAM_MODEL, pos, neg, z))[z] > 0,
                    oracle[z], reference, target=TARGET_DICE)  # fmt: skip
    lps = [oracle_image.TransformIndexToPhysicalPoint((c.point[0], c.point[1], z)) for c in clicks]
    steps = ", ".join(f"{'+' if c.positive else '-'}{c.point} -> {c.dice_after:.3f}" for c in clicks)
    print(f"plan: slice {z}, {len(clicks)} clicks vs {against}: {steps}")
    return {"nz": oracle.shape[0], "z": z, "clicks": clicks, "lps": lps, "against": against}


def wait_for_converted_mask(api, experiment: str, before: set[str]) -> tuple[str, str]:
    """The new ROI collection and the NIfTI export_mask put beside its DICOM-SEG; fails on timeout."""
    deadline = time.monotonic() + CONVERT_TIMEOUT_S
    while time.monotonic() < deadline:
        for assessor, files in seg_files(api, experiment).items():
            nifti = [f for f in files if f.endswith(".nii.gz")]
            if assessor not in before and nifti:
                return assessor, nifti[0]
        time.sleep(5)
    raise SystemExit(f"export_mask produced no NIfTI on {experiment} within {CONVERT_TIMEOUT_S}s")


def record(pw) -> Path:
    api = xnat_api(pw)
    before = set(seg_files(api, EXPERIMENT))
    steps_plan = plan(api, EXPERIMENT)
    browser = pw.chromium.launch(args=[f"--force-device-scale-factor={SCALE}", "--use-angle=swiftshader",
                                       "--enable-unsafe-swiftshader"])  # fmt: skip
    context = browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=SCALE)
    context.add_init_script(OVERLAY_JS)
    page = context.new_page()
    page.set_default_timeout(120_000)
    demo = Demo(page)
    try:
        assessor, nifti, stopped, frames = steps(page, demo, api, before, steps_plan)
    except Exception:
        page.screenshot(path=str(OUT / "failure.png"))  # what the recorder was looking at
        raise
    context.close()
    browser.close()
    api.dispose()

    video = OUT / "monailabel-demo.mp4"
    frames.encode(stopped, video)
    # Chapter times in seconds of the encoded video, which starts at the first screencast frame.
    start = frames.frames[0][1]
    chapters = [
        {"name": name, "start": round((t0 - start) / 1000, 2), "end": round((t1 - start) / 1000, 2)}
        for (name, t0), (_, t1) in zip(demo.marks, demo.marks[1:])
    ]
    result = {"experiment": EXPERIMENT, "assessor": assessor, "nifti": nifti, "chapters": chapters}
    (OUT / "result.json").write_text(json.dumps(result, indent=2))
    return video


def steps(page: Page, demo: Demo, api, before: set[str], plan: dict):
    """The recorded part: from the login page to the converted mask."""

    # Off camera: the login page and its fonts, so the video opens on a settled frame.
    page.goto(f"{XNAT}/")
    page.locator("form#login_form").wait_for()
    page.evaluate("() => document.fonts.ready.then(() => true)")

    frames = Frames(OUT / "frames")
    page.screencast.start(on_frame=frames.on_frame, size={"width": W * SCALE, "height": H * SCALE})

    demo.caption("Inside the trust: a clinician signs in to the trust's XNAT", 1500)
    form = page.locator("form#login_form")
    demo.type(form.locator("input[name='username']"), USER)
    demo.type(form.locator("input[name='password']"), PASSWORD)
    demo.click(form.locator("[type='submit'], button").first)
    page.wait_for_url(lambda url: "Login.vm" not in url)

    page.goto(f"{XNAT}/data/experiments/{EXPERIMENT}?format=html")
    demo.tidy()
    demo.caption("A spleen CT imported for an FL project, opened in the OHIF viewer", 2500)
    demo.click(page.locator("#ohifViewer"))
    page.wait_for_url(re.compile("/VIEWER/"))
    page.locator("canvas").last.wait_for(state="visible")
    page.wait_for_timeout(5000)

    demo.chapter("enable")
    demo.caption("Once per browser: switch on MONAI Label under Options → Preferences → Experimental", 1500)
    demo.click(page.locator("[data-cy=options-menu]"))
    demo.click(page.get_by_role("button", name="Preferences"))
    demo.click(page.locator("li[data-cy=experimental-features] button"))
    for feature in ("MONAILabel Tools", "Load image slices from the middle"):
        box = page.locator("label.featureLabel", has_text=feature).locator("xpath=following::input[1]")
        if not box.is_checked():
            demo.click(box)
    page.wait_for_timeout(800)
    demo.click(page.get_by_role("button", name="Save"))
    page.reload()
    page.locator("canvas").last.wait_for(state="visible")
    page.wait_for_timeout(5000)

    demo.chapter("sam-clicks")
    demo.caption("Start from nothing: a new, empty segment named spleen", 800)
    demo.click(page.get_by_text("Masks", exact=True))
    demo.click(page.get_by_role("button", name="Mask ROI"))
    demo.click(page.get_by_text("Unnamed Segment", exact=True))
    label = page.locator("input[value='Unnamed Segment']")
    demo.click(label)
    label.select_text()  # typing replaces it; the [value=...] match is gone once the text changes
    page.keyboard.type("spleen", delay=70)
    demo.click(page.get_by_role("button", name="Save"))
    page.get_by_text("spleen", exact=True).first.wait_for()
    page.wait_for_timeout(800)

    demo.caption("MONAI Label → DeepGrow → sam_2d: Segment Anything, prompted by clicks", 800)
    demo.click(page.locator("[data-cy=mask]"))
    demo.click(page.get_by_text("MONAILabel", exact=True))
    page.locator("#monaiToolList").wait_for()
    page.wait_for_timeout(1200)
    page.locator("#monaiToolList").select_option(label="DeepGrow")
    page.locator("select:near(:text('DeepGrow models'))").first.select_option(label=SAM_MODEL)
    # The panel hands the left button to the NVIDIA AIAA probe when it first connects, so a click
    # would go nowhere; picking the MONAILabel tool again gives it back to MONAI Label's probe.
    demo.click(page.locator("[data-cy=monailabel]"))
    page.wait_for_timeout(1000)

    demo.caption("Go to the spleen's largest slice", 600)
    # The viewer stacks the slices opposite to the mask's z axis: slider = slices - 1 - z.
    page.locator("input.imageSlider").fill(str(plan["nz"] - 1 - plan["z"]))
    page.wait_for_timeout(1500)

    clicks, lps = plan["clicks"], plan["lps"]
    for n, (click, point) in enumerate(zip(clicks, lps), start=1):
        if n == 1:
            text = "Click 1, positive, on the spleen"
        else:
            kind = "positive" if click.positive else "negative (Ctrl+click)"
            why = "where it missed" if click.positive else "where it leaked"
            why = why if click.fixes_error else "to tidy the edges"
            text = f"Click {n}, {kind}, {why}"
        demo.caption(text, 900)
        demo.prompt(click, point)
        outcome = "spills over the body" if click.dice_after < 0.5 else "follows the spleen"
        demo.caption(f"{text}: the mask {outcome} — Dice {click.dice_after:.3f} vs {plan['against']}", 2600)
    demo.caption(f"{len(clicks)} clicks, Dice {clicks[-1].dice_after:.3f}: the spleen, segmented from nothing", 2500)

    demo.chapter("export")
    demo.caption("Save it to XNAT: the mask becomes a DICOM-SEG ROI collection on the session", 800)
    demo.click(page.get_by_role("button", name="Export", exact=True).first)
    page.locator("input[value='spleen']").wait_for()
    page.wait_for_timeout(1200)
    demo.click(page.locator("button:right-of(input[value='spleen']):has-text('Export')").first)
    page.get_by_text("spleen", exact=True).first.wait_for()
    page.wait_for_timeout(2500)

    demo.caption("…and XNAT's export_mask command converts it to NIfTI for federated training", 500)
    assessor, nifti = wait_for_converted_mask(api, EXPERIMENT, before)
    page.goto(f"{XNAT}/data/experiments/{EXPERIMENT}/assessors/{assessor}?format=html")
    page.get_by_text("Resources", exact=True).wait_for()
    demo.tidy()
    demo.caption("The ROI collection in XNAT: its SEG resource now holds two files", 2500)
    demo.click(page.get_by_text("Manage Files", exact=True))
    # The SEG folder opens collapsed. YUI renames the toggle's class on hover (ygtvlp -> ygtvlph),
    # and the cursor glides onto it before clicking, so match either.
    demo.click(page.locator("table.ygtvdepth1 td[class*=ygtvlp]"))
    page.get_by_text(nifti).first.wait_for()
    demo.caption(f"{nifti} beside the DICOM-SEG, on the CT's own grid, ready for FL training", 4500)
    demo.caption("", 600)

    stopped = time.time() * 1000
    page.screencast.stop()
    demo.marks.append(("end", stopped))
    return assessor, nifti, stopped, frames


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        video = record(pw)
    print(f"Wrote {video}")


if __name__ == "__main__":
    main()
