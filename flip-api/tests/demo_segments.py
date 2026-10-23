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
"""The six on-camera segments of the demo video, scripted against the live dev stack.

Each function receives a ``Take`` whose page has not started recording yet, does whatever should
stay off camera (signing in, loading the page), calls ``rec.start()`` and plays its beat. The slow
platform waits between segments live in tests/demo_video.py.

Every request reaches the real stack: real sign-ins (Keycloak in dev), real trusts, real presigned
uploads, real training. Page content reuses some data-test hooks inside modals (the list rows carry
``project-name`` too), so modal fields are always scoped to the one open ``[role=dialog]``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Locator, Page, expect

from tests.demo_recorder import Take

UUID_PATH = r"[0-9a-f-]{36}$"


@dataclass(frozen=True)
class Persona:
    """A UI login. ``fallback`` marks the well-known admin standing in for a missing demo user."""

    email: str
    password: str
    role: str
    fallback: bool = False


def by_test(page: Page | Locator, name: str) -> Locator:
    return page.locator(f'[data-test="{name}"]')


def sign_in(rec: Take, persona: Persona, scenic: bool) -> None:
    """Sign in through the login form; ``scenic`` types it on camera, otherwise it is filled off camera.

    The password field is masked, so typing the real dev password on camera never shows it.
    """
    page = rec.page
    if "/auth/login" not in page.url:
        page.goto("/auth/login")
    username, password = by_test(page, "username"), by_test(page, "password")
    username.wait_for()
    if scenic:
        rec.type(username, persona.email)
        rec.type(password, persona.password)
        rec.click(by_test(page, "login-btn"))
        # The CLI warning about a fallback persona is invisible in the mp4, so say it on camera.
        rec.caption(
            f"Authenticating with the Central Hub… (demo {persona.role} user not provisioned — "
            "the well-known admin account plays this part)"
            if persona.fallback
            else "Authenticating with the Central Hub…",
            0,
        )
    else:
        username.fill(persona.email)
        password.fill(persona.password)
        by_test(page, "login-btn").click()
    page.wait_for_url(re.compile(r"/projects"), timeout=60000)


def click_each(rec: Take, locator: Locator, timeout_ms: int) -> None:
    """Click every match in turn, once at least one has rendered."""
    locator.first.wait_for(timeout=timeout_ms)
    for index in range(locator.count()):
        rec.click(locator.nth(index))


def strip_licence_header(sql: str) -> str:
    """Drop the leading comment block so the on-camera editor shows the query, not the licence."""
    lines = sql.split("\n")
    start = 0
    while start < len(lines) and (lines[start].startswith("--") or not lines[start].strip()):
        start += 1
    return "\n".join(lines[start:]).strip()


SET_EDITOR_JS = "(el, value) => { el.CodeMirror.setValue(value); el.CodeMirror.refresh(); }"


def create_project(
    rec: Take, researcher: Persona, name: str, description: str, query_sql: str, has_imaging: bool
) -> str:
    """Segment 1: sign in, check Connection Status, create the project, run the cohort query, stage it.

    Returns:
        str: The new project's id.
    """
    page = rec.page
    page.goto("/auth/login")
    by_test(page, "username").wait_for()
    rec.start()
    rec.caption("A researcher signs in to FLIP — the Federated Learning Interoperability Platform", 1400)
    sign_in(rec, researcher, scenic=True)

    by_test(page, "add-project-btn").wait_for()
    rec.caption("The researcher's project workspace", 700)
    rec.pause(1500)

    rec.caption("First, a look at Connection Status — is the federation healthy?", 700)
    rec.click(page.locator("a", has_text="Connection Status").first)
    by_test(page, "trust-row").first.wait_for()
    rec.pause(2200)
    rec.click(by_test(page, "view-toggle-radial"))
    by_test(page, "connection-radial-svg").wait_for(timeout=30000)
    rec.scroll_to(by_test(page, "radial-legend"), 0)
    by_test(page, "radial-legend").wait_for(timeout=30000)
    rec.caption("Topology view — the federation at a glance", 800)
    rec.pause(4000)

    rec.caption("Back to the workspace to set up the study", 600)
    rec.click(page.locator("a", has_text="Projects").first)
    by_test(page, "add-project-btn").wait_for()
    rec.pause(900)
    rec.caption("Creating a new research project", 700)
    rec.click(by_test(page, "add-project-btn"))
    dialog = page.locator("[role=dialog]")
    rec.type(by_test(dialog, "project-name"), name)
    rec.type(by_test(dialog, "project-description"), description, delay_ms=15)
    # "Includes imaging data" defaults on. Left on for a tabular study, the hub dispatches
    # CREATE_IMAGING and every trust queues pulls that can never resolve.
    if not has_imaging:
        rec.caption("This study is EHR-only — no imaging leaves the trust", 600)
        toggle = by_test(dialog, "has-imaging-toggle")
        rec.hover(toggle, 0.5, 0.5, 1600)
        rec.click(toggle)
        # Check what is drawn: the label, and the option that only exists while imaging is on.
        expect(dialog.get_by_text("Includes imaging data").locator("..").get_by_text("Disabled")).to_be_visible()
        expect(dialog.get_by_text("Convert DICOMs to NIfTI")).to_have_count(0)
        rec.caption("Imaging turned off — the hub creates no XNAT project and pulls nothing from PACS", 1800)
    rec.click(by_test(dialog, "create-project-btn"))

    page.wait_for_url(re.compile(r"/project/" + UUID_PATH))
    project_id = page.url.rsplit("/", 1)[-1]
    rec.caption("The new project starts life unstaged, with no cohort defined yet", 800)
    rec.pause(2200)

    rec.caption("Defining the patient cohort with an OMOP SQL query", 600)
    rec.click(by_test(page, "create-query-btn"))
    # Write the query in a few sweeps so it reads as authoring, not pasting.
    editor = page.locator(".CodeMirror")
    editor.wait_for(timeout=30000)
    lines = strip_licence_header(query_sql).split("\n")
    chunks = 4
    per_chunk = -(-len(lines) // chunks)
    for chunk in range(1, chunks + 1):
        editor.evaluate(SET_EDITOR_JS, "\n".join(lines[: chunk * per_chunk]))
        rec.pause(400)

    rec.pause(1000)
    rec.caption("The query runs inside each NHS trust — only aggregate counts leave the hospital", 800)
    rec.click(by_test(page, "view-cohort-query-results-btn"))
    page.get_by_text("Cohort Query Sent").first.wait_for(timeout=90000)

    rec.caption("Each trust runs the query against its own OMOP store and returns aggregates", 500)
    canvases = page.locator("canvas")
    canvases.first.wait_for(timeout=180000)
    rec.pause(1200)
    rec.scroll_to(canvases.first)
    rec.caption("Aggregate cohort characteristics, per trust — no patient-level data", 500)
    rec.pause(3000)
    rec.scroll_to(canvases.last)
    rec.pause(2800)

    rec.caption("Staging the project at the trusts that returned a cohort", 800)
    page.goto(f"/project/{project_id}")
    click_each(rec, page.locator('[data-test$="-selector"]'), 60000)
    rec.click(by_test(page, "stage-project-btn"))
    # The staging panel disappears once the project is STAGED.
    by_test(page, "stage-project-btn").wait_for(state="detached", timeout=90000)
    rec.caption("The project is now staged, awaiting administrator approval", 500)
    rec.pause(3000)
    return project_id


def approve_at_trust(rec: Take, trust_admin: Persona, project_id: str, trust_name: str) -> None:
    """Segment 2: one trust's Trust Admin approves the staged project for that trust, on My Trust.

    The other trusts are approved off camera (tests/demo_video.py): a second approval would look the same.
    """
    page = rec.page
    page.goto("/auth/login")
    by_test(page, "username").wait_for()
    rec.start()
    rec.caption(f"Each trust decides for itself — {trust_name}'s Trust Admin signs in", 1200)
    sign_in(rec, trust_admin, scenic=True)

    rec.caption("My Trust: the project requests staged at this trust", 700)
    rec.click(page.locator('a[href="/my-trust"]:visible').first)
    card = page.locator(f'[data-test="request-card"][data-project="{project_id}"]')
    card.wait_for(timeout=60000)
    rec.scroll_to(card, 800)
    rec.caption("The request shows the cohort found in this trust's data, and whether imaging is needed", 1500)

    rec.click(by_test(card, "approve-btn"))
    dialog = page.locator("[role=dialog]")
    by_test(dialog, "confirm-modal-btn").wait_for()
    rec.pause(1800)
    rec.click(by_test(dialog, "confirm-modal-btn"))
    page.get_by_text("Project approved", exact=True).first.wait_for(timeout=90000)
    rec.caption(f"Approved for {trust_name} — every other trust takes its own decision", 800)
    rec.pause(2500)


def approve(rec: Take, admin: Persona, project_id: str, has_imaging: bool) -> None:
    """Segment 2 without a demo Trust Admin: the administrator approves the staged project at every trust."""
    page = rec.page
    page.goto("/auth/login")
    by_test(page, "username").wait_for()
    rec.start()
    rec.caption("A FLIP administrator signs in to review the request", 1200)
    sign_in(rec, admin, scenic=True)
    rec.caption("The administrator sees every project on the platform", 800)
    rec.pause(1600)

    rec.caption("Opening the staged project for review", 600)
    page.goto(f"/project/{project_id}")
    rec.pause(2000)

    rec.caption("Approval authorises the imaging import at every selected trust", 800)
    click_each(rec, page.locator('[data-test^="trust-approve-"]'), 90000)
    rec.click(by_test(page, "approve-project-btn"))
    page.get_by_text("Project Approved").first.wait_for(timeout=90000)
    rec.pause(1500)

    # Only an imaging project dispatches CREATE_IMAGING, so only an imaging project grows per-trust
    # import bars; a tabular study is ready to train as soon as it is approved.
    if not has_imaging:
        rec.caption("No imaging to retrieve — the cohort is already at each trust", 600)
        rec.pause(4000)
        return
    rec.caption("Approval dispatched — the imaging import starts at each trust", 600)
    bars = page.locator('[data-test^="import-bar-"]')
    bars.first.wait_for(timeout=180000)
    rec.scroll_to(bars.first, 800)
    rec.pause(2500)
    # Hold until every trust has onboarded the project and its card is a live import bar.
    rec.caption("Each trust creates its XNAT project and begins retrieving the cohort", 600)
    expect(page.get_by_text("Awaiting creation")).to_have_count(0, timeout=240000)
    rec.pause(5500)


# Locate the middle labelled slice of the enrichment label (label_*.nii.gz beside the scan's NIfTI)
# BEFORE the mask is imported, match it to the stack on world z (not on index, which depends on how
# the viewer sorts the stack), and step there one slice at a time like a reader scrolling.
SCROLL_TO_LABELLED_SLICE_JS = """
async (experimentId) => {
  const scans = await (await fetch(`/data/experiments/${experimentId}/scans?format=json`)).json();
  let z = null;
  for (const scan of scans.ResultSet.Result) {
    const nifti = `/data/experiments/${experimentId}/scans/${scan.ID}/resources/NIFTI/files?format=json`;
    const files = await fetch(nifti);
    const label = files.ok ? (await files.json()).ResultSet.Result.find((f) => f.Name.startsWith("label_")) : null;
    if (!label) continue;
    let buffer = await (await fetch(label.URI)).arrayBuffer();
    const magic = new Uint8Array(buffer, 0, 2);
    if (magic[0] === 0x1f && magic[1] === 0x8b) {
      const inflated = new Blob([buffer]).stream().pipeThrough(new DecompressionStream("gzip"));
      buffer = await new Response(inflated).arrayBuffer();
    }
    // NIfTI-1 header: dim[1..3] at 42, bitpix at 72, vox_offset at 108, sform_code at 254, srow_z at 312.
    const header = new DataView(buffer);
    const le = header.getInt32(0, true) === 348;
    if (header.getInt16(254, le) <= 0) throw new Error(`${label.Name} has no sform`);
    const [nx, ny, nz] = [42, 44, 46].map((offset) => header.getInt16(offset, le));
    const plane = nx * ny * (header.getInt16(72, le) / 8);
    const voxels = new Uint8Array(buffer, header.getFloat32(108, le));
    const labelled = Array.from({ length: nz }, (_, k) => k)
      .filter((k) => voxels.subarray(k * plane, (k + 1) * plane).some((byte) => byte !== 0));
    if (!labelled.length) throw new Error(`${label.Name} is empty`);
    const k = labelled[Math.floor(labelled.length / 2)];
    z = header.getFloat32(320, le) * k + header.getFloat32(324, le);
    break;
  }
  if (z === null) throw new Error(`No label_* NIfTI on ${experimentId} to locate the segmentation`);
  const cs = window.cornerstone;
  const element = cs.getEnabledElements()[0].element;
  const stack = window.cornerstoneTools.getToolState(element, "stack").data[0];
  const offsets = stack.imageIds.map(
    (id) => Math.abs(cs.metaData.get("imagePlaneModule", id).imagePositionPatient[2] - z));
  const target = offsets.indexOf(Math.min(...offsets));
  const step = Math.sign(target - stack.currentImageIdIndex);
  for (let index = stack.currentImageIdIndex; index !== target; ) {
    index += step;
    stack.currentImageIdIndex = index;
    cs.displayImage(element, await cs.loadAndCacheImage(stack.imageIds[index]));
    await new Promise((resolve) => setTimeout(resolve, 40));
  }
  return target;
}
"""

# Once imported, sweep every slice the mask covers (to one end, the other, and back) so the
# segmentation reads as a volume. cornerstoneTools keys its labelmap by image index.
SWEEP_SEGMENTATION_JS = """
async () => {
  const cs = window.cornerstone;
  const element = cs.getEnabledElements()[0].element;
  const stack = window.cornerstoneTools.getToolState(element, "stack").data[0];
  const labelmap = window.cornerstoneTools.getModule("segmentation").getters.labelmap3D(element, 0);
  const slices = Object.keys((labelmap && labelmap.labelmaps2D) || {}).map(Number)
    .filter((i) => !Number.isNaN(i)).sort((a, b) => a - b);
  const middle = stack.currentImageIdIndex;
  for (const target of [slices[0], slices[slices.length - 1], middle]) {
    const step = Math.sign(target - stack.currentImageIdIndex);
    for (let index = stack.currentImageIdIndex; index !== target; ) {
      index += step;
      stack.currentImageIdIndex = index;
      cs.displayImage(element, await cs.loadAndCacheImage(stack.imageIds[index]));
      await new Promise((resolve) => setTimeout(resolve, 70));
    }
  }
}
"""

# The slice in view must carry the imported mask, so a z mismatch fails the take instead of
# filming an empty viewport.
SEGMENTATION_IN_VIEW_JS = """
() => {
  const cs = window.cornerstone;
  const element = cs.getEnabledElements()[0].element;
  const stack = window.cornerstoneTools.getToolState(element, "stack").data[0];
  const labelmap = window.cornerstoneTools.getModule("segmentation").getters.labelmap3D(element, 0);
  return Boolean(labelmap && labelmap.labelmaps2D && labelmap.labelmaps2D[stack.currentImageIdIndex]);
}
"""


def clear_xnat_session_mask(rec: Take) -> None:
    """Dismiss XNAT's "User session ended" dialog and its greyed "Session Expired" mask, if up.

    XNAT pops the dialog when an older session of the user is culled mid-page, and its timeout widget
    can read a stale expiration cookie and grey the whole page; a fresh load re-stamps the cookie.
    """
    page = rec.page
    ok = page.locator("button:visible", has_text=re.compile(r"^\s*OK\s*$"))
    if ok.count():
        ok.first.click()
    if page.get_by_text("Session Expired").count():
        page.reload()
        if ok.count():
            ok.first.click()


def xnat_ohif(
    rec: Take, username: str, password: str, xnat_project: str, experiment: str, seg_collection: str | None
) -> None:
    """Segment 3: inside one trust, the imported study in XNAT and in the OHIF viewer.

    ``seg_collection`` names the DICOM-SEG collection to import over the images; it is only passed
    when the session carries one, so the classification apps skip the mask beat.
    """
    page = rec.page
    page.goto("/")
    form = page.locator("form#login_form")
    form.wait_for()
    rec.start()
    rec.caption("Meanwhile, inside the trust: the cohort's imaging has been imported into XNAT", 1200)
    rec.type(form.locator("input[name='username']"), username)
    rec.type(form.locator("input[name='password']"), password)
    rec.click(form.locator("[type='submit'], button").first)
    page.wait_for_url(lambda url: "Login.vm" not in url)

    rec.caption("The trust's XNAT home — imaging lives here, inside the hospital", 600)
    clear_xnat_session_mask(rec)
    # Wait for the panels to be populated, not merely for the placeholder to go.
    page.get_by_text("Recent Data Activity").first.wait_for()
    page.get_by_text("Loading recent data").first.wait_for(state="detached")
    rec.pause(4500)

    rec.caption("Each FLIP project maps to an XNAT project holding its imported studies", 600)
    page.goto(
        f"/app/action/DisplayItemAction/search_value/{xnat_project}"
        "/search_element/xnat%3AprojectData/search_field/xnat%3AprojectData.ID"
    )
    rec.pause(2000)
    clear_xnat_session_mask(rec)
    rec.pause(2500)

    rec.caption("Opening one imported study in the OHIF DICOM viewer", 600)
    page.goto(f"/data/experiments/{experiment}?format=html")
    rec.pause(1500)
    # XNAT's own "View Images" action: a left click opens OHIF in this tab.
    rec.click(page.locator("#ohifViewer"))
    page.wait_for_url(re.compile(r"/VIEWER/"), timeout=180000)
    # Assert on the viewer's own chrome: a session bounce lands on an XNAT page instead.
    page.get_by_text(re.compile("back to xnat", re.I)).first.wait_for(timeout=180000)

    rec.caption("Reviewing an imported study in the OHIF DICOM viewer", 600)
    # The study viewport is the last canvas; the series thumbnail paints well before it.
    viewport = page.locator("canvas").last
    viewport.wait_for(timeout=180000)
    rec.pause(2000)
    if seg_collection:
        page.evaluate(SCROLL_TO_LABELLED_SLICE_JS, experiment)
        rec.pause(600)
    for fx, fy, dwell in [(0.42, 0.3, 1200), (0.55, 0.5, 1400), (0.45, 0.68, 1400), (0.6, 0.42, 1600)]:
        rec.hover(viewport, fx, fy, dwell)

    if not seg_collection:
        return
    rec.caption("The labels added during data enrichment travel with the imaging", 600)
    rec.click(page.get_by_text("Masks", exact=True).first)
    rec.pause(1200)
    rec.click(page.get_by_text("Import", exact=True).first)
    rec.pause(1600)
    rec.click(page.get_by_text(seg_collection).first)
    rec.pause(600)
    rec.click(page.get_by_text("Import selected").first)
    rec.caption("Segmentation overlaid on the source CT — never leaving the trust", 600)
    # The import decodes the SEG frame by frame; the labelmap is only complete once that settles.
    rec.pause(7000)
    if not page.evaluate(SEGMENTATION_IN_VIEW_JS):
        raise RuntimeError("the slice in view carries no imported segmentation")
    page.evaluate(SWEEP_SEGMENTATION_JS)
    rec.pause(800)
    rec.hover(viewport, 0.5, 0.45, 1800)
    rec.hover(viewport, 0.58, 0.55, 2200)


def create_model_and_train(
    rec: Take,
    researcher: Persona,
    project_id: str,
    has_imaging: bool,
    model_name: str,
    model_description: str,
    app_files: list[Path],
    backend_label: str,
) -> str:
    """Segment 4: create a model, upload the app (real presigned uploads) and start training.

    Returns:
        str: The new model's id.
    """
    page = rec.page
    sign_in(rec, researcher, scenic=False)
    page.goto(f"/project/{project_id}")
    by_test(page, "add-model-btn").first.wait_for()
    rec.start()
    rec.caption(
        f"{'Imaging is' if has_imaging else 'The cohort is'} in place — the researcher returns to the approved project",
        1000,
    )
    rec.pause(1500)

    rec.caption("Creating a model for this project", 600)
    rec.click(by_test(page, "add-model-btn").first)
    dialog = page.locator("[role=dialog]")
    rec.type(by_test(dialog, "model-name"), model_name)
    rec.type(by_test(dialog, "model-description"), model_description, delay_ms=15)
    rec.click(by_test(dialog, "create-model-btn"))
    page.wait_for_url(re.compile(r"/model/" + UUID_PATH))
    model_id = page.url.rsplit("/", 1)[-1]

    rec.caption(f"Uploading {backend_label} application bundle", 1000)
    rec.scroll_to(by_test(page, "upload-file-btn"), 0)
    # The visible control is a drop zone over a hidden <input type=file multiple>.
    by_test(page, "upload-file-input").set_input_files(app_files)
    # Each file does the presigned POST, the scan grace and the processing before it completes.
    expect(by_test(page, "file-upload-status-completed")).to_have_count(len(app_files), timeout=240000)
    rec.pause(1500)

    rec.caption("Confirming data enrichment and selecting the trusts to train at", 800)
    rec.click(by_test(page, "data-enrichment-btn"))
    click_each(rec, page.locator('[data-test^="trust-selection-"]'), 30000)

    rec.caption("Initiating federated training", 600)
    initiate = by_test(page, "initiate-training-btn")
    expect(initiate).to_be_enabled(timeout=90000)
    rec.click(initiate)
    # Once the hub accepts the run, the training actions replace the Initiate button.
    by_test(page, "stop-training-btn").wait_for(state="attached", timeout=90000)
    rec.caption("The job is queued to the FL network — each trust trains on its own data", 500)
    rec.pause(3000)
    return model_id


def follow_progress(rec: Take, researcher: Persona, project_id: str, model_id: str) -> None:
    """Segment 5: the live training dashboard, opened straight on the charts."""
    page = rec.page
    sign_in(rec, researcher, scenic=False)
    page.goto(f"/project/{project_id}/model/{model_id}")
    by_test(page, "training-timeline").wait_for()
    page.locator("canvas").first.wait_for(timeout=120000)
    rec.start()

    rec.caption("Later — training is running: weights travel to the hub, the data never does", 1000)
    rec.pause(2500)
    rec.caption("Live metrics stream back from every trust as the rounds progress", 600)
    rec.scroll_to(page.locator("canvas").first)
    rec.pause(2500)
    # The grid shows every metric in one frame, which reads better on camera than paging through.
    rec.caption("Every metric at once — each trust reporting its own curve", 600)
    rec.click(by_test(page, "metrics-view-grid"))
    by_test(page, "metrics-grid").wait_for()
    rec.pause(7000)


def download_results(rec: Take, researcher: Persona, project_id: str, model_id: str) -> None:
    """Segment 6: the finished run, and its aggregated results downloaded through a presigned URL."""
    page = rec.page
    sign_in(rec, researcher, scenic=False)
    page.goto(f"/project/{project_id}/model/{model_id}")
    by_test(page, "training-timeline").wait_for()
    # The download button and an empty chart both render before /metrics and /logs answer; a
    # rendered log line means the run's data has arrived, so the dashboard is shown complete.
    by_test(page, "download-results-btn").wait_for(state="attached", timeout=120000)
    page.locator("canvas").first.wait_for(timeout=120000)
    by_test(page, "log-timestamp").first.wait_for(timeout=120000)
    rec.start()

    rec.caption("Training complete — results are uploaded from the FL network", 1200)
    rec.pause(2000)
    rec.caption("The finished training curves, every trust side by side", 600)
    rec.scroll_to(page.locator("canvas").first)
    rec.click(by_test(page, "metrics-view-grid"))
    by_test(page, "metrics-grid").wait_for()
    rec.pause(3000)
    # The grid sorts by metric name, so an app with many TEST-* plots (xray) pushes its
    # training curves below the fold; scroll down to them.
    train_cell = page.locator('[data-test^="metrics-grid-cell-train" i]')
    if train_cell.count():
        rec.scroll_to(train_cell.first, pause_ms=1500)
        rec.pause(4000)
    rec.caption("Downloading the aggregated global model", 800)
    download_btn = by_test(page, "download-results-btn")
    rec.scroll_to(download_btn)
    expect(download_btn).to_be_enabled(timeout=120000)
    with page.expect_download(timeout=60000):
        rec.click(download_btn)
    rec.pause(3500)
    rec.caption("A model trained across every hospital — while the data never left any of them", 500)
    rec.pause(4000)
