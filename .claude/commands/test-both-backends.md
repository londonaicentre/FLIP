---
description: Run the full e2e smoke lifecycle on both FL backends (flower then nvflare), reusing the project to pull DICOM only once
allowed-tools: Bash(make:*), Bash(docker:*), Read
---

Drive the end-to-end smoke test (`make e2e_smoke`) across **both** FL backends in one go.
This is the scripted full-lifecycle check (create project → cohort query → image pull → FL
training → download results) and is **not** run in CI. It is heavy and long-running, so run
the smoke steps in the **background** and report each backend's result.

Use the documented backend-switch trick so the ~6-min DICOM image pull happens only **once**:
pull on the first backend, then reuse the same approved project on the second.

**Preconditions (check first, do not skip):**
- The stack must be running: `make up` (central hub + trusts + XNAT), trusts registered, Orthanc seeded. If it's not up, stop and tell the user to start it — do not try to `make up` yourself (it needs AWS access and is slow).
- If the user is testing branch code, the running containers must carry it. Remind them that the stack serves published images unless rebuilt (`make build-fl FL_BACKEND=<backend>` or the fast fl-api-only path), and they can confirm with `docker exec flip-fl-api-net-1 cat fl_api/utils/upload.py`.

**Steps:**

1. **Backend 1 — Flower (does the image pull).** Run in the background:
   ```
   make e2e_smoke FL_BACKEND=flower
   ```
   Watch the output for the `project_id=<UUID>` printed near the start — **capture it**; you need it for step 3. Wait for completion and report pass/fail. (Tutorial files default to `fl-tutorials/flower/xray_classification/` for this backend.)

2. **Switch the stack to NVFLARE** without disturbing the pulled DICOM (`restart-fl` leaves Orthanc/XNAT untouched):
   ```
   make restart-fl FL_BACKEND=nvflare DOCKER_FL_REGISTRY= DOCKER_FL_TAG=dev
   ```
   (Drop `DOCKER_FL_REGISTRY=`/`DOCKER_FL_TAG=dev` if the user is running published images rather than locally-built `:dev` images — ask if unsure.)

3. **Backend 2 — NVFLARE (reuses the project, skips re-pull).** Run in the background:
   ```
   make e2e_smoke FL_BACKEND=nvflare EXTRA_ARGS="--project-id <UUID-from-step-1>"
   ```
   The `--project-id` override skips cohort submission + approval and the image-pull wait returns immediately because the studies are already pulled. Report pass/fail.

4. **Summarise**: a clear PASS/FAIL line per backend, and surface any error output from a failed run. If the user passed extra flags in `$ARGUMENTS` (e.g. `--abort-midway`, custom `MODEL_FILES_DIR`/`QUERY_FILE`), thread them into the `EXTRA_ARGS`/make vars of both runs.

Note: if the first run fails before printing a `project_id`, stop and report — don't proceed to the switch.
