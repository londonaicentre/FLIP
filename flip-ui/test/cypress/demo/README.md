# FLIP end-to-end demo video recorder

Records the full platform walkthrough as one mp4 against the **live dev stack** — real Cognito sign-ins, real
trust round-trips, real S3 presigned uploads, real federated training. Six Cypress segments capture the on-camera
beats; the orchestrator (`flip-api/tests/demo_video.py`) performs the slow platform waits **off-camera** between
them and finally crops + concatenates the segment mp4s with [`../../../scripts/assemble-demo-video.sh`](../../../scripts/assemble-demo-video.sh).
Local dev tool — not run in CI, and entirely separate from the docs-GIF pipeline (`test/cypress/docs/`).

## Quick start

```bash
# once per environment
aws sso login --sso-session FLIP     # presigned URLs + Cognito admin calls need a live session
make demo-users                      # create the demo Cognito users (DEMO_*_PASSWORD from env)
docker restart flip-api              # boot seeding grants the demo users their roles

# record (from the repo root; ~30-45 min end to end, dominated by import + training)
make demo-video
# → flip-ui/test/cypress/demo/out/flip-demo.mp4   (~3 min, ~13 MB, 3840x2400 @ 30fps)
```

## The segments

| # | Spec | Persona | On-camera | Off-camera wait after |
|---|------|---------|-----------|----------------------|
| 1 | `01-create-project` | Researcher | Sign in → create project → project page → **Create Cohort Query** → write SQL → per-trust responses + aggregate charts → **stage the project** | cohort responses (already in) |
| 2 | `02-approve` | Admin | Connection Status (list + radial) → open staged project → approve trusts | imaging import (~6 min) |
| 3 | `03-xnat-ohif` | Trust user | XNAT login → project → OHIF DICOM view | — |
| 4 | `04-create-model-train` | Researcher | Create model → upload app files (presigned) → initiate training | training start + first metrics |
| 5 | `05-follow-progress` | Researcher | Live training timeline + metric charts | training finish (RESULTS_UPLOADED) |
| 6 | `06-download-results` | Researcher | Download the aggregated results | assembly |

## Iterating

Segment mp4s persist in `videos/` (`trashAssetsBeforeRuns` is off), so you can re-record just the part you're
working on and re-assemble:

```bash
make demo-video DEMO_ARGS="--project-id <uuid> --from-segment 4"   # keep segs 1-3, redo 4-6
make demo-video DEMO_ARGS="--video-scale 1 --skip-xnat"            # fast low-res draft
bash scripts/assemble-demo-video.sh test/cypress/demo/videos out/flip-demo.mp4 3   # re-assemble only (from flip-ui/)
```

A single segment can also be run standalone (see `run_segment` in `flip-api/tests/demo_video.py` for the exact
docker command and the `DEMO_*` env each spec requires). Segments hand ids forward through `state.json`
(gitignored): segment 1 writes `projectId`, segment 4 adds `modelId`; the orchestrator re-injects them via env.

## Recording contract (keep these in lockstep)

- **Geometry**: 1280x800 viewport rendered 1:1 inside a 1920x1200 Chrome window; the AUT sits at (540, 80) in the
  capture. `DEMO_VIDEO_SCALE` (orchestrator `--video-scale`, default 3) multiplies the framebuffer and the
  assemble-script crop by the same factor — the layout never changes, only pixel density (3 → 3840x2400).
  These constants are shared with the docs-GIF pipeline (`scripts/videos-to-gifs.sh`).
- **No mocking**: the demo support file must never import `globalIntercepts`.
- **Modals**: scope field interactions inside `[role=dialog]` — page content reuses data-test hooks.
- **Auth**: `demoLogin` completes a real Cognito SRP login, then satisfies the `VITE_E2E` route-guard seam with
  the real identity (see the rationale comments in `support/demoFlow.ts`).
- **XNAT**: reached via IPv4 literal from Python (the swarm ingress blackholes `::1`); the orchestrator sweeps the
  demo user's stale JSESSIONs before segment 3, and the spec reloads once after login to clear XNAT's stale
  session-expiration overlay.
- **Keep still pages moving**: Cypress records through a Chrome CDP screencast that only emits on compositor
  updates, so a page that paints once and then sits there (the OHIF viewer after it has drawn the study) yields
  almost no frames — the beat collapses in video time and can miss the frame that shows the image. Any dwell on a
  static page needs motion: `cy.demoHover(...)` walks the cursor overlay across an element for exactly this.
  The assembler warns when a segment keeps less than `DEMO_MIN_SEGMENT_SECONDS` (10s), which is the symptom.

All outputs here (`videos/`, `screenshots/`, `downloads/`, `out/`, `state.json`) are gitignored.
