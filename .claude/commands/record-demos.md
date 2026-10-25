---
description: Record the tutorial demo videos (xray, ehr, spleen) with make demo-video, check every take by frame, and hand over the mp4s
allowed-tools: Bash(make:*), Bash(docker:*), Bash(git:*), Bash(ffmpeg:*), Read
---

Record the walkthrough videos with `make demo-video` (`flip-api/tests/demo_video.py`; flags in
`flip-api/AGENTS.md`, "Demo Video Recorder"). `$ARGUMENTS` names the apps to record (default `xray ehr spleen`)
and any extra `DEMO_ARGS`.

The recorder drives the **running stack**. What appears on camera is whatever code that stack serves, not the
checkout the recorder runs from, and most bad takes so far came from the stack, not the recorder. Run every
command below on the host where the stack runs.

**Preconditions (check first, do not skip):**

1. **Nobody else is using the stack.** No `e2e_smoke`, `demo_video` or `restart-fl` process running
   (`ps -eo pid,etime,cmd | grep -E "[e]2e_smoke|[d]emo_video|[r]estart-fl"`). If the stack is shared, agree a
   window before recreating anything.
2. **The stack serves the code you mean to show.** For `flip-api`, `flip-ui` and each trust's `trust-api`,
   `imaging-api` and `data-access-api`, read the compose working dir
   (`docker inspect <c> --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'`) and the
   source of its bind-mounted code (`{{range .Mounts}}…{{end}}`), then `git -C <that checkout> log -1`. If it is
   behind the commit you are recording, stop and tell the user. Moving it means recreating those services from the
   recording checkout (`make up BUILD=true` for your own stack; for a shared one, only the affected services, with
   `up -d --no-deps --force-recreate -V --pull missing`: `-V` gives flip-ui a fresh `node_modules`, and
   `--pull missing` stops `pull_policy: always` from pulling the published image over your build). Rebuild flip-ui
   first when `flip-ui/package*.json` changed. Never `--remove-orphans`. Recreate trust services through the
   trust Makefile (a helper target that runs `$(ensure_snapshot_dir)` before its `up`), never with bare compose
   from a fresh checkout: otherwise Docker creates the cohort membership store
   (`trust/data-access-api/.snapshots/Trust_N`) owned by root, every approval's freeze fails ("Cohort snapshot
   store write failed"), and the take waits forever on an import that reads 0/0.
   After moving the hub past FLIP#857 (cohort snapshots), freeze the projects approved before it:
   `make -C flip-api backfill_cohort_snapshots EXTRA_ARGS="--dry-run"`, then without `--dry-run`.
3. **Migrations.** flip-api runs `alembic upgrade head` at boot. Before recreating it from a newer checkout, list
   which revisions in `flip-api/src/flip_api/db/migrations/versions/` the database does not have yet, and tell the
   user. Migrations are forward-only, so an older checkout's flip-api then fails to boot against that database.
4. **Demo users.** `DEMO_RESEARCHER_*`, `DEMO_ADMIN_*` and `DEMO_TRUST_ADMIN_*` set in `.env.development`, the users
   created with `make demo-users`, then flip-api restarted so boot seeding grants their roles and display names.
   Without a seeded profile the header shows the raw email. Without the Trust Admin, segment 2 falls back to the
   admin approving at every trust.
5. **Tooling.** Playwright's Chromium (`cd flip-api && uv run playwright install chromium`) and `ffmpeg` on `PATH`.
   Spleen also needs its labels (`make -C fl-tutorials download-spleen-data`).

**Steps:**

1. **One take at a time.** The takes share the FL nets, so record the apps in turn, in the background, each with
   its own log. Copy each final cut out of `flip-api/demo-video/out/` as soon as it lands, because the next fresh
   take wipes `flip-api/demo-video/segments/`.
   ```
   make demo-video DEMO_ARGS="--app xray"
   make demo-video DEMO_ARGS="--app ehr"
   make demo-video DEMO_ARGS="--app spleen --publish-segmentations --data-enrichment-cwd <abs>/fl-tutorials/datasets/spleen --data-enrichment-cmd '<label upload>'"
   ```
   The spleen enrichment command is the one in `flip-api/AGENTS.md`. Point it at both trusts' XNATs, using the IPv4
   literal (`127.0.0.1`), not `localhost`. A fresh take deletes every project the demo researcher owns outside the
   demo catalogue, so pass `--keep-project <uuid>` for any such project another tool still points at (e.g. a MONAI
   Label project).
2. **Watch for the end state, not just success.** Grep the log for `Demo video ready`, `Traceback`, `SmokeFailure`
   and `make: ***`. To stop a take, `kill` the `tests.demo_video` PID. Over ssh, never use `pkill -f <pattern>`: the
   pattern also matches the remote shell running it.
3. **A fix in a later segment** does not need a fresh take: copy segments 01–02 of the earlier take back into
   `flip-api/demo-video/segments/` and resume with `--project-id <uuid> --from-segment <n>`.
4. **Review every segment by frame before handing over.** Extract frames from each
   `flip-api/demo-video/segments/*.mp4` with `ffmpeg -i <segment>.mp4 -vf "fps=1/3,scale=1280:-1" <new dir>/f%02d.png`,
   and look at them. Known faults:
   - **An old page.** The model upload segment must show the current Prepare page, with its required files. Anything
     older means precondition 2 was skipped.
   - **"Degraded" on a trust card** right after the approval: a trust service's health probe took longer than
     `HEALTH_PROBE_DEGRADED_MS`. Find which probe before re-recording, rather than retaking until it doesn't show.
   - **A cohort chart showing a single "None" bar**, e.g. sex on the EHR cohort: a data or statistics bug. Report it.
   - **A raw email in the header**, or a "demo … user not provisioned" caption: precondition 4.
   - "Your password must contain a number" flashing while the password is typed is normal.
5. **Hand over.** Give the user each mp4's path and duration (`ffmpeg -i <f> 2>&1 | grep Duration`), and say which
   segments you checked by frame and what you saw. The output is 3840×2400 (16:10). Offer a 16:9 pad or crop if
   the videos are going to YouTube.
