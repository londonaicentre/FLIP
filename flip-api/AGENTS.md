# AGENTS.md — flip-api (Central Hub API)

## Service Overview

Central Hub REST API. FastAPI + psycopg2 + SQLModel (sync sessions). Handles user auth (Cognito), project management, trust coordination, FL run orchestration, cohort queries, file management, and scheduling.

## Key Files

| File | Purpose |
|------|---------|
| `src/flip_api/main.py` | FastAPI app factory, middleware, router registration |
| `src/flip_api/config.py` | Pydantic settings, env var loading |
| `src/flip_api/db/database.py` | SQLModel sync `Session` via `get_session()`; lazily-built engine; RDS Proxy + IAM auth `do_connect` hook in prod (FLIP#556); `with Session(...)` block load-bearing on error paths (FLIP#773) |
| `src/flip_api/db/models/main_models.py` | SQLModel ORM: Project, Trust, Model, File, etc. |
| `src/flip_api/db/models/user_models.py` | User, Role, Permission models |
| `src/flip_api/db/seed/` | DB seed data: roles, permissions, FL kit slots, FL scheduler, banners |
| `src/flip_api/db/migrations/` | Alembic migrations (`env.py`, `versions/`); `alembic.ini` at the service root. **Alembic owns the schema** |
| `src/flip_api/domain/schemas/` | Pydantic request/response schemas |
| `src/flip_api/domain/interfaces/` | Repository interfaces (Dependency Inversion) |
| `src/flip_api/auth/` | Cognito JWT verification, auth middleware |
| `src/flip_api/scripts/` | Trust registration + deletion CLIs (`register_trust.py`, `delete_trust.py`), internal-service-key + trust-key + XNAT-credential generation, demo user seeding (`create_demo_users.py`), env utils |

## Service Modules

| Module | Purpose |
| -------- | --------- |
| `user_services/` | Register, authenticate, update/delete users, roles, permissions |
| `project_services/` | Project CRUD, approval workflows |
| `model_services/` | ML model management, metrics, logs, approvals |
| `fl_services/` | FL training initiation, status, stop, per-net/quiesce status, job scheduling |
| `trusts_services/` | Trust registration, health checks, imaging creation |
| `cohort_services/` | Cohort query submission, results retrieval |
| `step_functions_services/` | Step function orchestration (register user, approve, cohort) |
| `file_services/` | S3 model-file upload/download, plus the scan-and-promote pipeline (`services/malware_scan_service.py`) that gates the `uploaded/` → `scanned/` quarantine boundary (#52) |
| `private_services/` | Trust-to-hub internal endpoints (tasks, cohort results) |
| `site_services/` | Site configuration, details |
| `role_services/` | Role CRUD |
| `scheduler/` | APScheduler background jobs (FL scheduling, trust polling, malware-scan reconcile sweep) |
| `utils/` | Cross-cutting helpers: `s3_client.py` (presigned POST/GET, `MAX_PRESIGNED_URL_TTL_SECONDS` clamp), `email_sender.py::send_templated_email` (SES/console dispatch, secret redaction), `encryption.py` (AES via `AES_KEY_BASE64`), `user_roles.py` (role/permission lookups + validation) |

## Commands (from `flip-api/`)

```bash
make test          # ruff + mypy + pytest (unit + integration)
make unit_test     # ruff + mypy + pytest unit + step function tests (--skip-client)
make integration_test  # Integration tests only
make local_test    # Tests without Docker (--skip-client --skip-db)
make lint          # ruff check --fix (in Docker)
make mypy          # mypy type check (in Docker)
make build         # docker compose build
make up            # Start flip-db then flip-api
make down          # Stop flip-api then flip-db
make debug         # Restart in debug mode (port 5678)
make migrate       # alembic upgrade head (apply migrations)
make migration MESSAGE="..."   # autogenerate a revision from the model diff (flip-db must be up)
make migration_downgrade       # alembic downgrade -1
make migration_history         # alembic history
make migration_current         # alembic current
make demo_video    # record the end-to-end demo video against the running stack (tests/demo_video.py; DEMO_ARGS=...)
make create_demo_users         # provision the demo Cognito users (DEMO_*_PASSWORD from env; restart flip-api after)
make seed_demo_projects        # seed the curated radiology catalogue (EXTRA_ARGS="--cleanup" removes it)
```

## Conventions

- FastAPI `Depends()` for DI. Repository pattern in `domain/interfaces/`.
- Sync SQLModel `Session` via `get_session()` dependency (`db/database.py`); the `with Session(...)` context is load-bearing on FastAPI error paths — a bare `yield` + `session.close()` strands the connection `idle in transaction` (FLIP#773).
- DB schema is owned by **Alembic** (`db/migrations/`), not `SQLModel.metadata.create_all`. The entrypoint runs `alembic upgrade head` before seeding at boot (fail-fast). Every schema-affecting change to `db/models/*.py` must ship a revision — the integration drift guard (`tests/integration/test_migrations.py`) enforces it. Native-PG-enum gotcha: `ALTER TYPE … ADD VALUE` needs `op.get_context().autocommit_block()`, and downgrades dropping an enum-typed table must `DROP TYPE`.
- pytest + factory_boy for test data. Fixtures in `conftest.py`.
- Ruff config (`[tool.ruff.lint]`, `preview = true`): line-length 120, select I/F/E/W/PT + UP006/UP007/UP035/UP042/UP045 (`UP042` enforces `StrEnum` over the legacy `(str, Enum)` pattern).
- All tests in `tests/unit/` and `tests/integration/`.

## End-to-End Smoke Test

`make e2e_smoke` (from root) drives a full project lifecycle against an **already-running stack**: create project → submit cohort query → wait for image pull → run FL training → download results. It is the scripted form of the manual UI sanity-check and is **not run in CI**. Long-running (image pull + FL training) — run it in the background.

Prerequisites:
- Stack up via `make up` (central hub + trusts + XNAT) with trusts registered; Orthanc PACS seeded with DICOM data so image pull has something to pull.
- The tutorial files are in-tree at `fl-tutorials/<backend>/` (NVFLARE and Flower both migrated from the legacy fl-base repos).

Defaults track `FL_BACKEND` (default `nvflare`): `MODEL_FILES_DIR` and `QUERY_FILE` point at `fl-tutorials/nvflare/image_classification/xray_classification/`. For Flower, they point at the in-tree `fl-tutorials/flower/xray_classification/`. Common overrides:

```bash
make e2e_smoke FL_BACKEND=flower                               # use the Flower tutorial
make e2e_smoke MODEL_FILES_DIR=/path/app QUERY_FILE=/path/q.sql
make e2e_smoke EXTRA_ARGS="--abort-midway"                     # exercise the FL stop-training path
make e2e_smoke EXTRA_ARGS="--image-pull-threshold 0.5 --image-pull-timeout 1200"
make e2e_smoke EXTRA_ARGS="--project-id <UUID>"               # reuse an approved project (see below)
make e2e_smoke EXTRA_ARGS="--trusts GSTT"                     # subset of trusts (codes/names, comma-separated);
                                                              # a registered-but-offline trust no longer blocks the run
```

The `--project-id <UUID>` override (printed as `project_id=<UUID>` at the start of any run) reuses an
existing approved project: it skips cohort submission + approval and jumps straight to model
create → upload → train → download. The image-pull wait still runs but returns immediately when the
studies are already pulled — so it lets you iterate on training/app code (and re-run after an upload
or fl-api change) without re-creating the project and re-pulling DICOM (~6 min/backend) each time.

**Smoking the spleen segmentation app requires a data-enrichment step (it needs labels).** *Data enrichment*
is the platform stage where a model developer adds whatever an app needs on top of the pulled imaging data in
XNAT (`docs/source/user-guides/user-data-enrichment.rst` — a project cannot start training until enrichment is
confirmed complete, even when nothing was added).

**Which supervised apps need it:** only those whose labels are **not in OMOP**. A segmentation mask is a 3D
volume with nowhere to live in the cohort query, so the spleen apps
(`fl-tutorials/<backend>/3d_spleen_segmentation*`) pair each converted `input_*.nii.gz` with a sibling
`label_*.nii.gz` that has to be uploaded to XNAT. The xray classification tutorial is the counter-example: its
labels *are* in OMOP, projected as dataframe columns by `query.sql` (`image_feature` → `observation`), and it
needs no enrichment. Skip a required enrichment and the smoke pulls, converts, starts training and then fails
with `No image/label pairs found: N image(s) …, none with a matching label_*.nii.gz` — which names the actual
cause. (Older app copies die with torch's opaque `num_samples=0` instead.)

`e2e_smoke` has a hook for exactly this: `--data-enrichment-cwd` + `--data-enrichment-cmd` run a shell command
**between the image pull and training**, with `FLIP_PROJECT_ID` exported. Since FLIP#776 the spleen uploader is
in-tree at `fl-tutorials/datasets/spleen/upload_spleen_labels_to_xnat.py` (the shared dataset-tooling tree)
— **no private repo required**. It resolves each trust's XNAT project by `secondary_ID == <FLIP project_id>`,
fetches the accession→MSD-case mapping at run time from the public `aicentreflip/trust-data` dataset
(`omop-csv/spleen_project/image_occurrence.csv` at the pinned data-version tag, which also carries `source_trust`), and writes each
label into the scan's existing `NIFTI` resource, renaming `input_` → `label_`. The XNAT protocol work lives in
`flip.xnat` (`flip-utils/flip/xnat/`), also exposed as the `flip-xnat` CLI.

**Enrich every trust, not one.** Each trust's XNAT holds only its own studies, so a trust left without
labels takes the run down at the zero-pairs guard. One invocation covers the roster: pass a
space-separated `XNAT_URLS` (credentials from `XNAT_USER`/`XNAT_PASS`) or repeat `XNAT_CREDENTIALS_FILES`
for per-trust logins. The whole mapping goes to every server and each ignores the others' studies, so no
per-trust splitting is needed. A run that resolves **no** destination anywhere now exits non-zero, so the
smoke can no longer walk past a wholly-skipped enrichment into a doomed training run (`--allow-no-op`
opts out). `TRUST=N` filters by the OMOP `source_trust` column (1=GSTT, 2=KCH) and is rarely needed —
note that is the OMOP partition, **not** the FL kit slot of the same `Trust_N` name.

Prereqs: `make -C fl-tutorials download-spleen-data NUM_CASES=41` (a smaller download enriches only part
of the cohort — the command warns, naming the missing cases) and `XNAT_USER`/`XNAT_PASS`. Standalone,
outside the smoke:

```bash
make -C fl-tutorials upload-spleen-labels FLIP_PROJECT_ID=<uuid> \
  XNAT_URLS="http://127.0.0.1:8105 http://127.0.0.1:8107" DRY_RUN=1   # then drop DRY_RUN
```

The mapping is cached beside the labels dir (so a re-run needs no huggingface.co egress) and
`HF_TRUST_DATA_REVISION=<sha|main>` overrides the dataset revision, which defaults to the tag in
`trust/.data_version`. The uploader speaks REST to XNAT, so those URLs carry each trust's
**`XNAT_WEB_PORT`** — 8105 (GSTT) and 8107 (KCH) since the FLIP#993 split, not 8104/8106, which are
now the DICOM SCP receiver ports. Dialling the old numbers reaches a DIMSE listener and hangs rather
than refusing the connection.

Through the smoke, `make -C flip-api e2e_smoke_spleen` (or `e2e_smoke_spleen_evaluation`) carries the
in-tree command already, targeting both dev trusts via `SPLEEN_XNAT_URLS` (override for another roster;
`SPLEEN_LABELS_DIR` likewise). The enrichment flags ride in `ENRICHMENT_ARGS`, deliberately *not*
`EXTRA_ARGS` — a command-line variable beats a target-specific one, so folding them together made
`make e2e_smoke_spleen EXTRA_ARGS="--project-id <uuid>"` silently skip enrichment. To invoke it directly
instead:

```bash
cd flip-api && uv run python -m tests.e2e_smoke \
  --model-files-dir ../fl-tutorials/nvflare/image_segmentation/3d_spleen_segmentation/app_files \
  --query-file ../fl-tutorials/nvflare/image_segmentation/3d_spleen_segmentation/query.sql \
  --data-enrichment-cwd ../fl-tutorials/datasets/spleen \
  --data-enrichment-cmd 'uv run --no-project --with ../../../flip-utils python upload_spleen_labels_to_xnat.py --flip-project-id "$FLIP_PROJECT_ID" --labels-dir ../../data/spleen/images --xnat-url http://127.0.0.1:8105 --xnat-url http://127.0.0.1:8107'
```

(The `$`-escaping trap still applies to any hand-written `EXTRA_ARGS`: `make -C flip-api …` expands once, so
`$$FLIP_PROJECT_ID` survives; the **root** `make e2e_smoke` re-expands through a second make and shell, so no
`$`-escape survives it — `$$` lands empty and `$$$$` injects the recipe shell's PID. Pass the id literally
there. The in-Makefile targets handle this for you. Note that `e2e_smoke_spleen` uploads the **Flower**
tutorial (`../fl-tutorials/flower/3d_spleen_segmentation/{app,query.sql}`), not the NVFLARE paths shown in the
direct-invocation example above — pair it with `FL_BACKEND=flower`, or invoke `tests.e2e_smoke` directly with
the NVFLARE paths for the NVFLARE tutorial. The enrichment step itself is backend-agnostic and runs out of the
shared `fl-tutorials/datasets/spleen/` tree either way.)

Enrichment must land **after** the pull and after DICOM→NIfTI conversion; the hook's position guarantees that.
The uploader derives each target filename from the converted `input_*.nii.gz`, so with no `NIFTI` resource it
skips every scan (reported as *skipped (no image in resource)*) — i.e. a broken XNAT Container Service surfaces
as "no labels".

**Smoking a REMOTE hub (stag / prod / an LZA account).** The smoke's base URL is `flip_api.utils.constants.BASE_URL`,
which defaults to `http://localhost:8080/api` — the *local dev hub* — unless `FLIP_E2E_BASE_URL` is set; forgetting it
hands your remote token to the dev hub, which answers `500 Internal server error during authentication`, and nothing on
the remote hub logs a request. Remote hubs enforce MFA, so the Cognito password flow is out: pass a pre-obtained bearer
as `FLIP_E2E_TOKEN` (the Cognito **AccessToken** of a TOTP-enrolled admin) plus `FLIP_E2E_REFRESH_TOKEN` so the smoke
can renew it across a long training run. The module also imports flip-api `Settings`, whose dev class requires
`POSTGRES_PASSWORD` even though the smoke never opens the database — export any dummy value on an LZA env, which has
none (IAM DB auth). Run it through the root target so `PROD` selects the env file:
`FLIP_E2E_BASE_URL=https://<edge>/api FLIP_E2E_TOKEN=… FLIP_E2E_REFRESH_TOKEN=… POSTGRES_PASSWORD=x make e2e_smoke_ehr PROD=lza-stag FL_BACKEND=nvflare EXTRA_ARGS="--trusts <CODE>"`.

**Tabular-only projects (no imaging stage — FLIP#1071).** A project created with "Includes imaging data"
off (`has_imaging=false`, creation-time and immutable like `dicom_to_nifti`) is approved without the
CREATE_IMAGING fan-out: no XNAT project, no accession-ids call, no pull, and `GET /projects/{id}/image/status`
returns `200 []`. The EHR risk-prediction tutorials are the first such cohort: `make e2e_smoke_ehr [FL_BACKEND=flower]`
(root or `-C flip-api`) picks that tutorial for the backend and pins `--no-imaging`, which creates the project
with the flag off and skips the image-pull wait (the smoke reads `has_imaging` back off the project, so
`--project-id` reuse honours it too; `make e2e_smoke EXTRA_ARGS="--no-imaging"` is the generic form). The flag
rides in `TARGET_ARGS`, a third recipe slot placed between `ENRICHMENT_ARGS` and `EXTRA_ARGS`, for the same reason
enrichment does not use `EXTRA_ARGS`. Prereq on the dev stack: `make -C trust load-synthea-ehr` on every trust
(TRUST_INDEX=1 OMOP_DB_PORT=5434, TRUST_INDEX=2 OMOP_DB_PORT=5436; a kind/Helm trust has no host port — port-forward
form in `trust/deploy/helm/README.md`, "Local clusters (kind)"), then restart both data-access-apis if the
EHR query was ever submitted before the load (results are cached by query text). Imaging projects
get fast feedback instead: submitting a cohort whose explicit SELECT list
has no `accession_id` column is a 400 at submission (hub pre-check, not a security control — `SELECT *` passes
through to the trust's authoritative check).

**Testing a change on BOTH FL backends in one sitting (the backend switch).** The pulled DICOM lives in
each trust's Orthanc/XNAT, which `make restart-fl` leaves untouched — so you can pull once on the first
backend and *reuse the same project* for the second, skipping the second image pull entirely:

```bash
# 0. Rebuild the fl-api image(s) from your branch first — the running stack serves PUBLISHED images,
#    not your code. Full: `make build-fl FL_BACKEND=<backend>`; fl-api-only fast path:
#    DOCKER_GID=$(id -g) docker compose -f fl-services/<backend>/compose.dev.yml build <fl-api|flip-fl-api>
make e2e_smoke FL_BACKEND=flower                                          # note the printed project_id=<UUID>
make restart-fl FL_BACKEND=nvflare DOCKER_FL_REGISTRY= DOCKER_FL_TAG=dev  # switch backend onto rebuilt :dev images
make e2e_smoke FL_BACKEND=nvflare EXTRA_ARGS="--project-id <UUID>"        # reuse project → skip cohort + re-pull
```

Before trusting either run, confirm the live container actually carries your code (the stack silently
runs old images otherwise): `docker exec deploy-fl-api-net-1-1 cat fl_api/utils/upload.py`
(the compose SERVICE name — dev containers set no `container_name`, so they are named by the project). See
[`fl-tutorials/AGENTS.md`](../fl-tutorials/AGENTS.md) for `make build-fl` / `:dev` image details.

## Demo Video Recorder

`make demo-video` records the full end-to-end walkthrough (researcher creates project + cohort → admin checks
Connection Status, stages + approves → XNAT/OHIF DICOM view at a trust → model + app upload → training → results
download) against the **live dev stack** and assembles one mp4. Local dev tool, not run in CI. Six Cypress segments
(`flip-ui/test/cypress/demo/`, config `flip-ui/cypress.demo.config.ts` — a live-wired fork of the docs-GIF harness
with a `demoCaption` subtitle verb) run in Docker (`cypress/included`, `--network host`); the slow platform waits
(cohort responses, ~6 min imaging import, FL training) happen **off-camera** between segments via
`flip-api/tests/demo_video.py`, which reuses `tests/e2e_smoke.py`'s wait functions and finally calls
`flip-ui/scripts/assemble-demo-video.sh` (same crop constants as `videos-to-gifs.sh`). Prerequisites: stack up,
live AWS SSO session, and ideally the demo Cognito users — `make demo-users` (reads `DEMO_RESEARCHER_PASSWORD` /
`DEMO_ADMIN_PASSWORD` from env, never committed) then restart flip-api so seeding grants their roles; without them
the recorder falls back to the well-known admin for both parts. Useful `DEMO_ARGS`: `--app ehr` (record the tabular EHR
risk-prediction tutorial: the project is created with imaging off and the XNAT segment is skipped), `--app spleen` (record the 3D
spleen segmentation tutorial instead of chest X-ray; pair with `--data-enrichment-cwd/-cmd` for the off-camera
label upload, same contract as `e2e_smoke`), `--publish-segmentations` (republish those NIfTI labels as DICOM-SEG
ROI collections via `flip-api/tests/xnat_seg_upload.py` so segment 3 shows the segmentation overlaid in OHIF —
dcmqi in Docker for the conversion, the viewer's own `PUT /xapi/roi/…?type=SEG` for the upload; `--seg-limit`
caps how many sessions per trust are converted), `--skip-xnat`, `--project-id <uuid> --from-segment <n>` (iterate
on later segments without re-running the pull), `--trusts GSTT`, `--fl-backend flower`, `--video-scale 1` (fast
drafts; the default 3 renders the browser at 3× device pixels so the 1280x800 viewport is captured at 3840x2400 —
4K-class). Segment mp4s + the final video land under `flip-ui/test/cypress/demo/` (gitignored). The final mp4 is
named for the recorded app (`flip-demo-xray.mp4` / `flip-demo-spleen.mp4`).

## Hub Environment Variables

Auth, email, and the model-file upload/scan pipeline. The cross-cutting keys
(`AES_KEY_BASE64`, the internal-service keys, `CENTRAL_HUB_API_URL`) stay in the root
[`AGENTS.md`](../AGENTS.md#key-environment-variables); FL knobs live in
[`../fl-services/AGENTS.md`](../fl-services/AGENTS.md).

- Database auth — In production (`ENV=production`) flip-api authenticates to Postgres through **RDS Proxy** using a short-lived AWS IAM auth token minted per-connection by a SQLAlchemy `do_connect` hook in `flip_api/db/database.py` (passwordless engine URL, `sslmode=require`, `pool_pre_ping` + `pool_recycle`). The proxy reaches RDS with the rotating RDS-managed master secret it re-reads natively, so the app holds no static DB credential and secret rotation no longer causes an outage (FLIP#556). Dev (`ENV=development`) uses the static `POSTGRES_PASSWORD` from the environment. There is no separate toggle — the path is selected by `ENV`.
- `ENFORCE_MFA` — `true` (the `Settings` default; do **not** set in `.env*` files for stag/prod) gates every authenticated route on TOTP enrolment via the app-layer MFA check in `verify_token`. The dev override lives in `deploy/compose.development.yml` (`ENFORCE_MFA=false`) so local development doesn't force enrolment on a burner authenticator app. Production compose (`compose.production.yml`) passes `ENFORCE_MFA=${ENFORCE_MFA:-true}` so the env var can be overridden from `.env.stag`/`.env.prod` for testing, but falls back to the secure `true` default when unset. Intentionally not in `.env.development.example` or AWS Secrets Manager — the Settings default (`true`) is the canonical secure anchor. The UI mirrors this flag from `/users/me/mfa/status` and skips the enrolment redirect when it's false.
- `EMAIL_BACKEND` — `ses` (send through AWS SESv2) or `console` (log the would-be email instead). The **default** is selected per environment class in `flip_api/config.py` rather than by an env file: `DevSettings` defaults to `console`, so a local stack needs no SES identity, templates or verified address (FLIP#919), and `ProdSettings` narrows the type to `Literal["ses"]`, making `console` a boot-time `ValidationError` in stag/prod. A developer can still opt into the SES path by setting `EMAIL_BACKEND=ses` (plus the two `AWS_SES_*` addresses) in `.env.development`; `deploy/compose.development.yml` passes all three through to flip-api, since that service declares an explicit `environment:` list and no `env_file`, so a variable it does not name cannot reach the app however the env file is written. A dedicated flag rather than an `ENV` branch (same rationale as `ENFORCE_MFA`) so tests and local experiments can select the SES path without also flipping DB auth, encryption and docs exposure. The base `Settings` default (`ses`) is *not* a safety net for a misconfigured deploy — an unset `ENV` resolves to `DevSettings` and hence `console` — it exists so the field is declared for `ProdSettings` to narrow. All email dispatch goes through `flip_api/utils/email_sender.py::send_templated_email`; the console backend redacts secret-shaped keys (`setup_path` included) so the decrypted XNAT set-password link never reaches the logs. The two `AWS_SES_*` addresses are consumed only by the SES path and remain **required** in stag/prod.
- `MAX_MODEL_FILE_BYTES` — Hard cap on the file size of a single model-file upload, in bytes. Bound on the presigned POST policy so S3 rejects oversized payloads at the edge — the hub never sees them. Default `5368709120` (5 GiB) — raised from 100 MiB so large evaluation checkpoints (e.g. the ~759 MiB Ark+ weights, ~1.5 GiB for the multimodel variant) can be uploaded; such checkpoints are staged server-side by the FL API and loaded by the fl-server, not bundled into the app deployed to clients. The S3 policy condition allows a small fixed overhead above this for multipart/form-data framing (see `_MULTIPART_OVERHEAD_BUFFER_BYTES` in `flip_api/utils/s3_client.py`); the UI guard at `flip-ui/src/utils/file.ts` compares against this raw value so a file at exactly the cap is accepted on both sides.
- `PRE_SIGNED_URL_EXPIRATION_SECONDS` — Setting default for presigned-URL TTLs, in seconds: the model-file presigned POST policy (upload) and the presigned GET URLs for model-file downloads (FLIP#784). The hub silently clamps to the 1800s (30 min) security ceiling encoded as `MAX_PRESIGNED_URL_TTL_SECONDS` in `flip_api/utils/s3_client.py` (a leaked URL is a capability against the bucket in either direction — writable for POST, readable for GET — so the leak window must stay tight). The ceiling is 1800s rather than the original 600s to give larger transfers (up to the `MAX_MODEL_FILE_BYTES` cap) enough time to complete. Setting default equals the ceiling (1800) so default-configured deployments never trip the clamp; over-ceiling operator values leave a warning in the logs and are clamped.
- `UPLOADED_MODEL_FILES_BUCKET` / `SCANNED_MODEL_FILES_BUCKET` — the two model-file prefixes, and the platform's quarantine boundary (FLIP#52). Researcher uploads are written to the `uploaded/` staging prefix by the presigned POST policy; `POST /files/process-scanned-file/{model_id}/{file}` registers the row as `SCANNING` and schedules the scan, which promotes clean files into `scanned/` and deletes rejected ones. Every consumer — the FL app bundler (`fl_services/services/fl_service.py`), model-file downloads, and listings — reads `scanned/` only, so an unscanned or rejected file can never be bundled to a trust. **They must point at distinct prefixes**: pointed at the same location the promote step degrades to a no-op (logged) and the boundary disappears.
- `ALLOWED_MODEL_FILE_EXTENSIONS` — extension whitelist enforced before a presigned POST policy is minted, so a disallowed type never reaches S3. JSON list or comma-separated string, matched case-insensitively; default `.py .json .toml .pt .pth .pkl .txt .yaml .yml .safetensors`. `.toml` is **required by the Flower backend** — every Flower app and tutorial ships a `config.toml` run-config that fl-api-flower feeds to `flwr run --run-config`, so dropping it rejects every Flower upload. Opaque archives (`.zip`/`.tar`) are excluded — the scan pipeline cannot gate their contents. A value that normalises to nothing (`",,,"`) falls back to the default rather than an empty list, which would otherwise reject everything (or, for `PICKLESCAN_FILE_SUFFIXES`, silently skip all scanning). A rejection returns 400 with the allowed set, which the UI surfaces verbatim.
- `PICKLESCAN_FILE_SUFFIXES` / `PICKLESCAN_TIMEOUT_SECONDS` — which uploads get a structural picklescan before promotion (default `.pt .pth .pkl .pickle`) and the wall-clock cap per scan (default 120s). Dangerous globals mark the file `INFECTED` and delete the object; a scan that errors or times out fails closed to `ERROR`. Signature-based AV (GuardDuty Malware Protection for S3) is tracked separately in FLIP#838.
- `BANDIT_TIMEOUT_SECONDS` — wall-clock cap (default 60s) on the non-blocking Bandit pass over `.py` uploads (FLIP#877, GHSA-8465). Unlike `PICKLESCAN_TIMEOUT_SECONDS` a timeout here just means no findings are recorded (fail-open, never `ERROR`) — Bandit is advisory only and never gates promotion. Findings land on `UploadedFiles.bandit_findings` (`[]` = scanned clean, `NULL` = never scanned) and surface in the UI as an amber indicator. `bandit` is a dependency baked into the `flip-api` image, not the mounted `src/`: under the dev pull-by-default sourcing above, running without `BUILD=true` after picking up this dependency serves an image with no `bandit` binary, so every upload silently records `NULL` findings while the UI and docs still claim a scan ran.
- `SCHEDULER_MALWARE_SCAN_RECONCILE_RATE` — how often (minutes, default 1) the sweep re-checks uploads left `SCANNING` by an app restart mid-scan.
