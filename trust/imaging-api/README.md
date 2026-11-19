<!--
    Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at
        http://www.apache.org/licenses/LICENSE-2.0
    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.
-->

# Imaging API

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![FLIP Imaging API CI](https://github.com/londonaicentre/FLIP/actions/workflows/test_trust_imaging_api.yml/badge.svg)](https://github.com/londonaicentre/FLIP/actions/workflows/test_trust_imaging_api.yml)
[![imaging-api](https://ghcr-badge.egpl.dev/londonaicentre/imaging-api/latest_tag?trim=major&label=imaging-api)](https://github.com/londonaicentre/FLIP/pkgs/container/imaging-api)
[![Coverage](https://codecov.io/gh/londonaicentre/FLIP/branch/main/graph/badge.svg?flag=imaging-api)](https://codecov.io/gh/londonaicentre/FLIP)

The **imaging-api** is a Trust-side service that manages imaging data operations within the FLIP platform. It
interfaces with [XNAT](https://www.xnat.org/) for imaging project management and [Orthanc](https://www.orthanc-server.com/)
as the mock PACS source. It is called by the [trust-api](../trust-api/) and, through the [`flip` package](../../flip-utils/flip/), by the FL clients (see [Authentication](#authentication)).

Tested with `XNAT version 1.10.2.1` (DQR 3.1.0, Container Service 3.9.0).

## Role in the FLIP Platform

The imaging-api handles the imaging data lifecycle for federated learning studies:

1. **Project creation** — creates XNAT projects and user accounts for approved FL studies
2. **DICOM import** — queries the PACS with accession numbers and queues image retrieval into XNAT, all through XNAT's DQR REST API (XNAT talks to Orthanc over DIMSE; imaging-api itself never contacts Orthanc)
3. **Data download** — packages and transfers XNAT datasets for FL training
4. **Upload** — stores result files back into XNAT experiments
5. **Retrieval status** — monitors import progress through XNAT's REST API (DQR's import queue and history, and XNAT's direct-archive and prearchive sessions)

## Deployment

The imaging-api starts as part of the Trust-side stack:

```bash
make up-trusts
```

It requires both [XNAT](../xnat/) and [Orthanc](../orthanc/) to be running.

## API Reference

### Download

Download and unzip a XNAT dataset to a local folder.

```json
{
  "encrypted_central_hub_project_id": "string",
  "accession_id": "string"
}
```

Query parameters:

| Parameter | Values | Default |
| --- | --- | --- |
| `assessor_type` | `scan` or `assessor` | `scan` |
| `resource_type` | `NIFTI`, `DICOM`, `SEG` or `ALL`: a closed allow-list matching flip-utils' `ResourceType` enum, since the value is interpolated into the XNAT download URL; anything else is a 422 | `NIFTI` |
| `force_refresh` | `true` or `false` | `false` |

`accession_id`, like `scan_id` and
`resource_id` on the upload route, must be a single RFC 3986 path segment (unreserved characters only, not `.` or
`..`); other values are rejected with a 422 before any XNAT request is made (#908).
Every XNAT request URL in `services/` and `routers/` is built with the shared `xnat_url` builder (#1193, #1386). The
one exception is the session login in `utils/xnat_token.py`, which appends two constant paths to the base URL it is
constructed with. The builder quotes raw path segments and encodes query parameters separately, protecting direct
service calls as well as validated routes. Empty identifiers and exact dot-segments are rejected before HTTP dispatch
because URL quoting alone does not prevent their normalization. `tests/utils/test_no_raw_xnat_urls.py` fails if a
string is built from `XNAT_URL` anywhere else in `imaging_api/`; the XML namespace registered in `services/projects.py`
is its one exemption.

Downloads are cached on the trust host. Extraction lands in
`<BASE_IMAGES_DOWNLOAD_DIR>/<net_id>/<central_hub_project_id>/<accession_id>/` and a completeness sentinel
(`.flip_complete-<assessor_type>-<resource_type>`) is written there only after successful extraction, so FL training
code that fetches the cohort every round stops re-downloading bytes already on disk. Sentinels match exactly per
(assessor, resource); the per-project path segment keeps projects that share an accession from being served each
other's copies.

The sentinel records XNAT's experiment ID and the resource stats (scan, label, resource ID, file count, size) read
just before the download. A hit makes one call, `GET /data/experiments/{id}/scans/ALL/resources?format=json`, and
compares them:
- **Unchanged:** the cached folder is returned.
- **Changed, or the experiment gone:** that resource's cached files (`scans/*/resources/<R>/`, or all of `scans/` for
  `ALL`) and the sentinels covering them are deleted, then it is downloaded again. Deleting rather than merging keeps
  files XNAT has since deleted off disk; other resource types stay cached. This is how labels uploaded straight to
  XNAT (`flip.xnat` enrichment, the UI) and a NIfTI conversion that finished after an `ALL` download are seen.
- **XNAT cannot answer:** the cached copy is served with a warning. The request's session check passed moments
  before, and a failure would make an FL client skip the accession for that round.
- **Not detected:** a file replaced by one of the same name and size (file digests are empty on FLIP's XNAT, whose
  `checksums` setting is off); `force_refresh=true` covers it. Assessor (`SEG`) downloads are not checked, as XNAT
  has no `assessors/ALL/resources` listing.
- **A sentinel with no readable record** (empty or corrupt) is a miss: its copy is deleted and downloaded again once
  XNAT answers the lookups, and never served unchecked.

`force_refresh=true` takes the same delete-then-download step. The cache is also invalidated when an upload through
imaging-api changes the accession (see Upload), and — on the FLARE backend, where a job mounts the net's whole
folder — by `CleanupImages`, which empties that folder at job start and end. A Kubernetes job pod mounts only its
project's folder, read-only, or none, and deletes nothing: there the retention sweep bounds the cache.

**Retention (FLIP#1050).** Every cache hit refreshes the sentinel's mtime, the entry's last-used time, and an hourly
sweep (`services/cache_retention.py`) removes accession folders whose newest sentinel is older than
`IMAGE_CACHE_RETENTION_HOURS` (default `168`; `0` turns the sweep off). A folder's own mtime counts too, so a
half-extracted study is never expired, and the sweep runs on the single-worker event loop, yielding only between
studies, so a check and removal never interleave with a download or a hit. Net folders and `upload/` are never touched. This is what bounds the cache on Flower,
whose clients read a study every round, and on NVFLARE job pods; an NVFLARE client reads a study once per job, so the
TTL must be far longer than any job. An expired study is downloaded again from XNAT on its next request.

The download URL pins `structure=default`, XNAT's own default. Every zip member then sits under the session label,
which FLIP sets to the accession id at import, so a study extracts as
`<accession_id>/scans/<scan id>-<description>/resources/<resource>/files/…`. FL apps read that layout (the prostate
tutorial matches the `<scan id>-<description>` folder), and XNAT's other layouts break them: `improved` drops the
session folder, `simplified` and `legacy` reshape the folders below it. A zip with no files, or with a member not
lexically under `<accession_id>/`, is refused before anything is extracted (HTTP 500) and never marked complete. The
check reads the member names, not the folder afterwards, because a re-download lands in an already-populated folder.

`POST /download/images/{net_id}/stream` takes the same body and query and, after the same cache fill, answers with
the cached study as an uncompressed tar rooted at `<accession_id>/` (`application/x-tar`, the cache's sentinels left
out) instead of a path. It is for a client with no mount of the net's images folder, such as an NVFLARE job pod on
Kubernetes (FLIP#1049): flip-utils uses it when `IMAGES_TRANSPORT=stream` and extracts the tar into the job's own
directory.

### Imaging

Interfaces with XNAT's DICOM Query-Retrieve (DQR) plugin. Full DQR API docs available at
`http://127.0.0.1:<XNAT_WEB_PORT>/xapi/swagger-ui.html#/dicom-query-retrieve-api` — `XNAT_WEB_PORT`
is the per-trust host port for XNAT's web UI in the kit file (`trust/.env.<CODE>.<env>`), e.g. `8105`
for the GSTT dev trust and `8107` for KCH. Not `XNAT_PORT`, which since FLIP#993 is the DICOM SCP
receiver port (`8104`/`8106`) and serves no HTTP.

- Query PACS with an accession number
- Queue image retrieval from PACS to an XNAT project

DQR responses may omit `studyDescription` and `referringPhysicianName`, as anonymised studies often do.
Missing values default to empty strings; no placeholder tags need to be added to the source DICOMs.
The study UID and accession number remain required for import.

There is no PACS id to configure:

- **Query:** sends no `pacsId`, so DQR runs it against the PACS XNAT flags as its default query/retrieve PACS. That is the one registration `configure-xnat.sh` keeps, with that flag set.
- **Import:** DQR does not default the same way, so each import reads that PACS's id from `GET /xapi/pacs?queryable=true` (one call per import, never cached). It fails, naming what XNAT reported, when no PACS or more than one is flagged.
- **Import run:** checks that PACS once before its C-FINDs. Without it, the run logs that message and stops, rather than every query failing 404 and the run ending "No studies found to import".
- **`GET /imaging/ping_pacs`** without an id pings the same PACS, resolved the same way on every call.

To fix a missing PACS: re-run XNAT's configure step to restore a missing flag. A PACS disabled in DQR's admin page has to be re-enabled there.

Example DQR import request (imaging-api fills `pacsId` from `GET /xapi/pacs?queryable=true`, as above):

```json
{
  "pacsId": 1,
  "aeTitle": "XNAT",
  "port": 8104,
  "projectId": "b83294b0-4ff8-4629-bfb3-4cb587e87756",
  "forceImport": true,
  "studies": [{
    "studyInstanceUid": "1.2.826.0.1.3680043.8.274.1.1.8323329.1190647.1740750893.798613",
    "accessionNumber": "FAK77115197"
  }]
}
```

### Projects

Create an XNAT project from a Central Hub project:

```json
{
  "project_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "trust_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "project_name": "my_project",
  "query": "some query",
  "users": [
    {
      "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "email": "user@company.com",
      "is_disabled": false
    }
  ]
}
```

Creating the project also creates its DICOM->NIfTI trigger: an XNAT event subscription named
`DICOM-NIfTI Conversion`, active when the project's `dicom_to_nifti` is set. It launches the site's one
`dcm2niix-scan` wrapper on `DCM2NIIX_IMAGE` (from `GET /xapi/commands/available/site`), under the action
key XNAT lists for that wrapper id (`GET /xapi/events/allactions`), and refuses with the registered
images named when there is no such wrapper (FLIP#1451, FLIP#1456).

#### Converting the backlog: `reconvert_nifti`

`python -m imaging_api.scripts.reconvert_nifti` (`make -C trust reconvert-nifti KIT=<CODE>` on Compose)
lists the archived scans that have DICOM and no NIfTI and, with `--launch`, converts them by one
Container Service bulk launch per project. See
[`../deploy/helm/TROUBLESHOOTING.md` §2.3e](../deploy/helm/TROUBLESHOOTING.md#23e-scans-archived-without-nifti-converting-the-backlog).

#### Converting on Snowflake: `CONVERTER_BACKEND=snowflake`

Snowpark Container Services has no Docker socket, no swarm and no Kubernetes backend, so XNAT's
Container Service cannot launch dcm2niix there at all. `CONVERTER_BACKEND` selects which machinery
converts, and it stays at `container-service` on Compose and Kubernetes, which are unchanged.

On `snowflake` (`imaging_api/services/snowflake_converter.py`, FLIP#1583):

1. imaging-api CALLs `APP.CONVERT_SCAN(project, session, scan)` instead of launching a wrapper. That
   is an owner's-rights procedure owned by `FLIP_<CODE>_CONVERTER` and created by Terraform
   (FLIP#1581), because launching a job needs `CREATE SERVICE` and a service's OAuth token carries
   the role that OWNS it: granting the services role that privilege would let the fl-client, which
   runs researcher code, start arbitrary containers. The procedure validates the three identifiers
   and launches a specification fixed in Terraform, so the image, pool, command and mounts are not
   parameters this service can influence.
2. It polls `APP.CONVERT_STATUS`. An empty status (`[]`) means the job exists and has not been
   scheduled yet, which is **pending**: reading it as a failure relaunches a healthy conversion on
   every poll.
3. It reads the converter's output from its own mount of the `CONVERTER_IO` stage, at
   `<CONVERTER_IO_DIR>/<project>/<session>/<scan>/`, and registers it in XNAT over REST as a `NIFTI`
   resource (`PUT .../resources/NIFTI`, then one `PUT .../files/<name>?inbody=true` per file). That
   is the step the Container Service output handler performed before; nothing else does it here.

`session` is the XNAT session **label**, because the archive tree is
`<project>/arc001/<label>/SCANS/<scan>/DICOM` and that is the path the procedure mounts; the REST
registration addresses the **experiment id**. Both appear on this path and they are not the same.

`reconvert_nifti` takes the same route (`make -C trust/deploy/snowflake reconvert-nifti KIT=<CODE>`
runs it as a job service). The scan selection is identical, because it reads XNAT and nothing else.
Two things it cannot do there: `--max-attempts` has nothing to act on, since `CONVERT_SCAN` drops
and replaces a scan's job on each launch and leaves no history to count; and there is no
wrapper-image check, since the image is fixed in the procedure rather than chosen per launch.

The credential is the OAuth session token SPCS mounts at `SNOWFLAKE_TOKEN_FILE`
(`authenticator="oauth"`). There is no password and no private key on this path, and
`snowflake-connector-python` is imported lazily so a Compose trust never loads it.

### Retrieval

Get import status or count for a project:

```
project_id: 8ba38209-97f5-41b9-976e-dfe3c5c8dd94
query: SELECT * FROM omop.image_occurrence
```

Each cohort accession gets one status, the first in this table that applies:

| Status | When |
| --- | --- |
| `successful` | It is an experiment in the project |
| `failed` | DQR executed its request and the request `FAILED` while no direct-archive or prearchive session is receiving the study (DQR 3.1.0 marks every C-MOVE into the direct archive `FAILED`, files or not, FLIP#1816); or its direct-archive or prearchive session is in `ERROR` (XNAT moves a failed direct-archive into the prearchive and deletes its row, FLIP#1511) |
| `processing` | Any other executed request, or a direct-archive or prearchive session in another status (DQR's `RECEIVED` means the study was transferred, not archived) |
| `queued` | Its request waits in DQR's queue |
| `queue_failed` | None of these exists |

The DQR records come from XNAT's REST API, not its database (`services/import_requests.py`): DQR's
`GET /xapi/dqr/import/queue` and `/import/history`, which take no project filter, so they are read with
`all=true` and filtered by `xnatProject`; `POST /xapi/direct-archive` with a `project` filter; and
`GET /data/prearchive/projects/{project}`. A retry re-archives a prearchive `ERROR` session
(`POST /data/services/archive?overwrite=append`) and pulls from the PACS again only what that does not
recover, deleting its stale prearchive session first. When every
accession is already an experiment they are not read at all. A failed read is a 500, never the 404 the
hub reads as "the XNAT project is missing" (FLIP#1022).

The history only grows, so it is read newest first, 1,000 rows a page, and stops a day before the
project's `insert_date`. The flip-ui polls a project's imaging status every 10 s while its page is open,
and each poll of a project with unarchived accessions reads the history written since that project was
created, about 0.6 MB per 1,000 rows.

### Upload

Upload files to an XNAT experiment:

```json
{
  "encrypted_central_hub_project_id": "<base64 AES-256-GCM envelope of the project id, as issued by the hub>",
  "accession_id": "FAK09131796",
  "scan_id": "12345",
  "resource_id": "RES",
  "files": [
    "FAK09131796/scans/1_2_826_.../resources/NIFTI/files/input.nii"
  ],
  "exist_ok": true
}
```

`exist_ok` (default `false`): `true` replaces a file of the same name already in the resource, `false`
leaves it in place and fails the request.

A missing resource is created with the first file, and a missing `scan_id` with the type of the session's first
scan; in a session with no scans, the upload fails.

`PUT /upload/images/{net_id}/files` is the multipart twin for a client with no mount of the net's upload folder
(FLIP#1049): the same fields as form data, the files themselves as `files` parts, each named by the same path
relative to the net's upload folder that `files` holds above. They are staged at `<net_id>/upload/<request id>/<path>`
for the upload and removed afterwards; a path that would leave its request's folder is refused with a 400.

### Users

Create users, update user details, or add a user to an XNAT project.

#### Imaging follows the approved cohort

A patient who leaves a project's approved cohort (the national data opt-out, a removal from OMOP) stops being served
by data-access-api at once (FLIP#857). Their imaging follows (FLIP#1860), from the same list: the accessions
data-access-api serves for the project now (`/cohort/accession-ids`).

| What | When | How |
| --- | --- | --- |
| Downloads | Within a minute | The route answers `404` for an accession that is not on the list. The list is read at most once a minute per project. |
| The cached copy | Within `COHORT_RECONCILE_MINUTES` (15) | `<net_id>/<central_hub_project_id>/<accession_id>/` is removed in every net's folder. |
| XNAT | After `COHORT_REMOVAL_GRACE_HOURS` (24) off the list | The session is deleted from the project's archive and prearchive with its files, then the subject when none of their studies is left. The log names the project and the accession when it is first seen off the list, and again at each removal. |

- **Why XNAT waits.** A cached copy is downloaded again on demand, but only a new pull from the PACS refills XNAT,
  and the list is short for a while whenever a trust reloads its OMOP tables. An accession that is back on the list
  before the wait ends is kept, and the wait starts again if it leaves again. The count is held in memory, so a
  restart of imaging-api starts it again: a removal can come late, never early.
- **When nothing is removed.** While data-access-api cannot answer, or refuses the whole list (`403`): a refusal does
  not say whether the cohort fell below the trust's threshold or its lookup failed. Downloads are refused in both
  cases (`503` and `404`). An empty list is an answer, not a refusal: the approved cohort has no imaging, and the
  project's sessions are removed like any other that is off the list.
- **What is removed.** Imaging sessions the project owns whose label is not a served accession, with their files. XNAT keeps no copy of them while its `backupDeletedToCache` site setting is off, its default (FLIP does not set it). An assessor (a
  segmentation) goes with its session, never on its own; a session shared in from another project is left to that
  project.
- **Not pulled again.** A study removed this way is not imported again if the patient returns to OMOP. Their rows are
  served again (the frozen membership still holds them), the download answers `404`, and the import status shows the
  study as processing.

## Configuration

Key environment variables. Most come from the trust kit file — template at [`../.env.example`](../.env.example), copied per trust to `trust/.env.<CODE>.<env>` — but `XNAT_URL` and `DATA_ACCESS_API_URL` are internal-topology constants with defaults in `imaging_api/config.py`, and the composes do not inject them into this service (`trust/deploy/compose/compose_trust.development.yml` says as much: an empty `${VAR}` from a kit that carried them would override the code default). `XNAT_URL` is deliberately absent from the kit template; `DATA_ACCESS_API_URL` is in it only because the fl-client reads it from env.

| Variable | Description |
| --- | --- |
| `XNAT_URL` | URL of the XNAT instance (default `http://xnat-web:8080` — a compose-topology constant, not a kit field) |
| `XNAT_PORT` | **Required.** XNAT's DICOM SCP receiver port. Paired with `XNAT_AETITLE` as the C-MOVE destination handed to the PACS, so it must be the receiver port, not the web-UI `XNAT_WEB_PORT` |
| `XNAT_AETITLE` | XNAT's own AE title (default `FLIP_XNAT`). Must match `XNAT_AETITLE` in `configure-xnat.sh`: DQR matches the C-MOVE destination against a registered SCP receiver by exact AE title **and** port |
| `XNAT_SERVICE_USER` | XNAT service account username |
| `XNAT_SERVICE_PASSWORD` | XNAT service account password |
| `DATA_ACCESS_API_URL` | Internal URL of the data-access-api (default `http://data-access-api:8000`) |
| `DCM2NIIX_IMAGE` | Container Service image used for automatic DICOM→NIfTI conversion. The default is `ghcr.io/londonaicentre/xnat-dcm2niix:<version>` at the tag `ARG DCM2NIIX_VERSION` in `trust/xnat/dcm2niix/Dockerfile` names — the one source of truth, which `trust/xnat/dcm2niix/check_image_pin_sync.sh` (pre-commit + CI, FLIP#980) checks `imaging_api/config.py` against. The per-project event subscription looks the XNAT command up by this **exact** image string, so it must match what `trust/xnat/xnat/config/dcm2niix_command.json` registers — pinned by version tag, never `latest` |
| `BASE_IMAGES_DOWNLOAD_DIR` | **Required.** Root of the downloaded-images tree as seen **inside the container** — the composes and the Helm chart inject the literal `/app/data/images`. The kit file's same-named `BASE_IMAGES_DOWNLOAD_DIR` is a different value: the *host* directory the composes bind-mount onto that path (the same host-vs-container dual meaning the trust-api README notes for the kit's `OMOP_DB_PORT`). imaging-api mounts the whole tree; each fl-client mounts only its own `net-N` slice |
| `COHORT_RECONCILE_MINUTES` | How often imaging-api brings each project's cached studies and XNAT sessions to its approved cohort (default `15`; see "Imaging follows the approved cohort"). `0` turns the removal off; a download of an accession that left the cohort is refused either way |
| `COHORT_REMOVAL_GRACE_HOURS` | How long an accession must have been off a project's approved cohort before its XNAT session is deleted (default `24`). The cached copy does not wait |
| `CONVERTER_BACKEND` | Which machinery converts DICOM to NIfTI: `container-service` (default, Compose and Kubernetes) or `snowflake` (SPCS, where Container Service cannot run). See "Converting on Snowflake" above. A setting rather than an auto-detection, because "am I on SPCS?" has no reliable answer and a wrong guess silently picks a converter that cannot run |
| `SNOWFLAKE_HOST`, `SNOWFLAKE_ACCOUNT` | Injected into every SPCS container by the platform; not set by the spec, which is why both default to empty. Read only when `CONVERTER_BACKEND=snowflake` |
| `SNOWFLAKE_DATABASE`, `SNOWFLAKE_SCHEMA` | Where the converter procedures live: `FLIP_TRUST_<CODE>` and `APP` |
| `SNOWFLAKE_TOKEN_FILE` | The SPCS OAuth session token (default `/snowflake/session/token`). This **is** the credential; the service spec must set `mountSessionToken: true` |
| `CONVERTER_IO_DIR` | This container's mount of the `CONVERTER_IO` stage (default `/converter-io`), where the converter job writes and imaging-api reads |
| `CONVERT_POLL_SECONDS`, `CONVERT_TIMEOUT_SECONDS` | Seconds between `CONVERT_STATUS` polls (default 15) and the ceiling on one scan's conversion (default 1800) |
| `REIMPORT_STUDIES_ENABLED` | Whether the retry-failed-imports route is available (default `true`). When `false`, `PUT` to the reimport endpoint returns `418` with "Reimport studies feature is not enabled" |
| `AES_KEY_BASE64` | AES-256 key shared with the hub, used to open the AES-256-GCM-enveloped project identifiers the FL client forwards (FLIP#1179). Must be byte-identical to the hub's and to trust-api's; a mismatch fails closed |
| `TRUST_INTERNAL_SERVICE_KEY_HEADER` | Header name for trust-internal service auth (default `X-Trust-Internal-Service-Key`) |
| `TRUST_INTERNAL_SERVICE_KEY` | Per-trust plaintext key. Validated as inbound auth on every router except `/health`, and forwarded outbound on calls to data-access-api `/cohort/accession-ids`. |

## Authentication

imaging-api exposes privileged XNAT operations via a service account. To prevent any container on the trust Docker network — or any operator with SSM port-forward access — from acting as that service account, every router except `/health` requires callers to send `TRUST_INTERNAL_SERVICE_KEY` in the configured header. imaging-api compares the header against its own copy of the same per-trust key with a constant-time compare.

Direct callers in this repo: trust-api. The fl-client container calls imaging-api indirectly via
the [`flip` Python package](../../flip-utils/flip/)
(consumed by both FLARE and Flower fl-client / fl-server images) —
`flip.get_by_accession_number(...)` and friends read `TRUST_INTERNAL_SERVICE_KEY` from
`os.environ` and add the header to every HTTP request. User training code (`client_app.py`,
`server_app.py`, tutorials) doesn't see the header.

imaging-api is also a *sender*: it forwards the same key on every call to data-access-api `/cohort/accession-ids`, whose `/cohort` router runs the same auth check.

Each trust has a distinct key. A trust's `TRUST_INTERNAL_SERVICE_KEY` is minted by `register_trust` (`make -C deploy/providers/AWS register-trusts KIT=<CODE> HUB=<hub>` on a deployed hub, `make register-trusts` in dev) and written into that trust's kit file (`trust/.env.<CODE>.<env>`), which `trust/Makefile` `-include`s so every trust-internal container inherits it.

For more on the threat model, see
[Trust-internal service authentication](../../docs/source/security.rst#trust-internal-service-authentication).

## Further Reading

- [XNAT setup](../xnat/README.md)
- [Orthanc setup](../orthanc/README.md)
- [Trust deployment overview](../README.md)
- [Contributing & Development Guide](../../CONTRIBUTING.md)
