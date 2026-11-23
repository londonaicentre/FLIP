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
- The stack must be running: `make up` (central hub + trusts + XNAT), trusts registered, Orthanc seeded. If it's not up, stop and tell the user to start it — do not try to `make up` yourself (it is slow, and needs the trusts' XNAT plugin cache from `make -C trust prepare-artifacts`).
- If the user is testing branch code, the running containers must carry it. Remind them that the stack serves published images unless rebuilt (`make build-fl FL_BACKEND=<backend>` or the fast fl-api-only path), and they can confirm with `docker exec deploy-fl-api-net-1-1 cat fl_api/utils/upload.py`.

**Steps:**

1. **Backend 1 — Flower (does the image pull).** Run in the background:
   ```
   make e2e_smoke FL_BACKEND=flower
   ```
   Watch the output for the `project_id=<UUID>` printed near the start — **capture it**; you need it for step 3. Wait for completion and report pass/fail. (Tutorial files default to `fl-tutorials/flower/image_classification/xray_classification/` for this backend.)

2. **Switch the stack to FLARE** without disturbing the pulled DICOM (`restart-fl` leaves Orthanc/XNAT untouched):
   ```
   make restart-fl FL_BACKEND=nvflare DOCKER_FL_REGISTRY= DOCKER_FL_TAG=dev
   ```
   (Drop `DOCKER_FL_REGISTRY=`/`DOCKER_FL_TAG=dev` if the user is running published images rather than locally-built `:dev` images — ask if unsure.)

3. **Backend 2 — FLARE (reuses the project, skips re-pull).** Run in the background:
   ```
   make e2e_smoke FL_BACKEND=nvflare EXTRA_ARGS="--project-id <UUID-from-step-1>"
   ```
   The `--project-id` override skips cohort submission + approval and the image-pull wait returns immediately because the studies are already pulled. Report pass/fail.

4. **Summarise**: a clear PASS/FAIL line per backend, and surface any error output from a failed run. If the user passed extra flags in `$ARGUMENTS` (e.g. `--abort-midway`, custom `MODEL_FILES_DIR`/`QUERY_FILE`), thread them into the `EXTRA_ARGS`/make vars of both runs.

Note: if the first run fails before printing a `project_id`, stop and report — don't proceed to the switch.

## Against a deployed hub (staging)

When the user asks for the smoke against staging (or another deployed hub), the steps above do not apply as written:
the FL backend is the hub's, switched by a Terraform apply rather than `restart-fl`, so one run covers one backend.
Run the hub's current backend and say the other was not covered. The full reference is "Smoking a REMOTE hub" in
`flip-api/AGENTS.md`.

1. **Same build everywhere.** Read the hub's version from `/api/health`, then each trust's from trust-api `/health`
   (`version`, plus `hub_key_match: true`). If a trust is behind, upgrade it first: `make -C trust upgrade-trust` on
   Compose, `make -C trust/deploy/helm upgrade-trust-k8s` on Kubernetes, each with `TAG=<the hub's sha- tag>`. The
   upgrade refuses a tag any site image was never built at; dispatch the trust image builds on that commit first when
   it did not trigger them. On Helm, check the FL client pod afterwards: the upgrade can exit 0 with it down (#1984).
2. **Tokens.** Remote hubs enforce MFA, so the smoke takes a Cognito access token and refresh token from a signed-in
   admin's browser. Ask the user to copy each from the DevTools console with
   `copy(localStorage.getItem(Object.keys(localStorage).find(k => k.endsWith('.accessToken'))))` (then
   `.refreshToken`) and save it themselves with `! umask 077; pbpaste > <file>`. The refresh token lets the run outlive
   the access token's hour.
3. **Run it in the background** from a checkout of the deployed commit, with the hub's env file selected by `HUB`:
   ```
   FLIP_E2E_BASE_URL=https://<hub>/api FLIP_E2E_TOKEN="$(cat <access>)" FLIP_E2E_REFRESH_TOKEN="$(cat <refresh>)" \
     POSTGRES_PASSWORD=x make e2e_smoke HUB=stag FL_BACKEND=<hub's backend> \
     EXTRA_ARGS="--trusts <CODE>,<CODE> --project-name '<name>' --project-description '<the clinical task>'"
   ```
   For a trust without the OMOP vocabulary, pass the tutorial's `query_novocab.sql` as an absolute `QUERY_FILE`.
4. **Trusts that run NVFLARE jobs as pods** need their study entries once the project is approved, before training
   starts: `make -C trust/deploy/helm sync-project-studies KIT=<CODE> HUB=stag` on that trust (no `PROJECTS` = every
   approved project). Without it the server aborts the job on every site. The other trusts then show only
   `child worker process finished with RC 1`, which is that abort, not a crash. If the run has already failed, sync,
   then re-run with `--project-id <UUID>`; the images are already pulled.
5. **When the model goes to ERROR**, read the FL server's log before the trusts': on AWS, the CloudWatch group
   `/ecs/fl-server-net-1` names the site and the reason.
