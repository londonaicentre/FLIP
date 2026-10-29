# FLIP trust on Snowflake (Snowpark Container Services)

The Snowflake shape of a FLIP trust node, beside [`helm/`](../helm/) (Kubernetes) and the
`compose_trust.*.yml` files (Compose on a host). Same stack, same kit file, different
substrate: every container runs as a Snowpark Container Services (SPCS) service, and the
trust's OMOP lives in native Snowflake tables.

Tracking issue: FLIP#1582, part of the epic FLIP#1589.

> **Not production yet.** The target is a test account proving the implementation, built
> to production security standards so the same code can be used by an NHS trust. Three
> spikes are still open and may change details here: see
> [Assumptions pending spikes](#assumptions-pending-spikes).

## What lives where

| Concern | Owner |
|---|---|
| Roles, warehouse, compute pools, database, schemas, stages, image repository, network rules | Terraform root `trust/deploy/terraform/snowflake/` (FLIP#1581) |
| Images, SECRET values, service specifications, the services themselves | This directory |
| Object names both of the above use | [`naming.py`](naming.py), from the written contract |

Terraform creates **no secret values**. They are written only by `make sync-kit`, from the
trust's kit file, and never pass through Terraform variables or state.

## Files

| File | What it is |
|---|---|
| `naming.py` | The object-naming contract: roles, warehouse, pools, database, schemas, stages, secrets, service names and the derived SPCS DNS names |
| `spec_templates.py` | One specification template per service. The compose files are the source of truth for env, ports and volumes; this is their SPCS translation |
| `render_specs.py` | Reads the kit, writes `rendered/<CODE>.<env>/<service>.yaml` plus `manifest.json` |
| `yaml_min.py` | A small YAML emitter, so the renderer stays stdlib-only |
| `kitfile.py` | Kit-file parsing, shared by the renderer and the kit sync |
| `mirror_images.py` | GHCR to the account image repository, linux/amd64, digests recorded in `images.lock.json` |
| `sync_snowflake_kit.py` | SECRET objects, the FL kit onto the `FL_KIT` stage, the governance document onto `GOVERNANCE`. The twin of `helm/sync_k8s_kit.py` |
| `spcs_sql.py` | Emits the deploy SQL (`up`/`down`/`suspend`/`status`/`selftest`), and runs the one-shot jobs (`run-job`/`job-status`); the Makefile pipes it |
| `scripts/` | What the one-shot job services run: `xnat-configure-spcs.sh`, `seed-orthanc-spcs.sh`, `smoke-cstore-spcs.sh`. Repo files so they can be read and reviewed on their own; `render_specs.py` inlines each into its spec |
| `tests/` | The guards. No Snowflake account, no credentials, no network |

`rendered/` and `images.lock.json` are generated and gitignored.

## Quick start

```bash
# 0. One-off: a snow CLI connection with key-pair auth for the deploy SERVICE user,
#    FLIP_<CODE>_DEPLOY_USER, whose default role is FLIP_<CODE>_DEPLOY.
#    Humans use MFA; no ACCOUNTADMIN in automation.
snow connection add --connection-name flip-test ...

# 1a. Account security objects, break glass, rarely, by a person user with MFA (FLIP#1581)
make -C trust/deploy/terraform/snowflake-platform apply KIT=GSTT HUB=stag

# 1b. Infrastructure, the workload root (FLIP#1581)
make -C trust/deploy/terraform/snowflake apply KIT=GSTT HUB=stag

# 2. Images into the account repository (needs docker + GHCR login + egress)
make -C trust/deploy/snowflake mirror-images KIT=GSTT HUB=stag

# 3. Secrets, FL kit, governance document
make -C trust/deploy/snowflake sync-kit KIT=GSTT HUB=stag

# 4. Render and deploy
make -C trust/deploy/snowflake up KIT=GSTT HUB=stag

# 5. Configure XNAT. NOT optional: until this has run XNAT has registered no DICOM
#    receiver and rejects every C-STORE, so the trust answers HTTP and retrieves nothing.
make -C trust/deploy/snowflake xnat-configure KIT=GSTT HUB=stag
make -C trust/deploy/snowflake job-status     KIT=GSTT HUB=stag JOB=xnat-configure

# 6. Seed the mocked PACS, once the DICOM is on the SEED stage (see below), then prove
#    the DICOM path works end to end.
make -C trust/deploy/snowflake seed-orthanc KIT=GSTT HUB=stag
make -C trust/deploy/snowflake smoke-cstore KIT=GSTT HUB=stag

# 7. Check, and stop paying when idle
make -C trust/deploy/snowflake status  KIT=GSTT HUB=stag
make -C trust/deploy/snowflake suspend KIT=GSTT HUB=stag
```

Every Snowflake target takes `DRY_RUN=true`, which prints the SQL instead of running it:

```bash
make -C trust/deploy/snowflake up KIT=GSTT HUB=stag DRY_RUN=true
```

That is not only a safety net. It is how the deploy path is tested, since CI has no
Snowflake account and no credentials.

`HUB` maps to the kit-file token (`trust/.env.<CODE>.<token>`) through
`deploy/env_mode.mk`, the repo's single copy of that map, exactly as the Helm Makefile
does. `SNOW_CONNECTION` selects the snow CLI connection and defaults to `flip-test`.

## The services

One SPCS service per container. All endpoints `public: false`.

| Service | Storage | Secrets mapped | Endpoints |
|---|---|---|---|
| `trust-api` | none | TRUST_API_KEY, TRUST_INTERNAL_SERVICE_KEY, AES_KEY_BASE64 | HTTP 8000 |
| `data-access-api` | stages COHORT_SNAPSHOTS, GOVERNANCE (read-only) | TRUST_INTERNAL_SERVICE_KEY, AES_KEY_BASE64 | HTTP 8000 |
| `imaging-api` | stage IMAGES (all nets) | TRUST_INTERNAL_SERVICE_KEY, AES_KEY_BASE64, XNAT_SERVICE_PASSWORD | HTTP 8000 |
| `fl-client-net-<N>` | stage IMAGES `net-<N>/` only, stage FL_KIT `net-<N>/` read-only | TRUST_INTERNAL_SERVICE_KEY only | none |
| `xnat-web` | stages XNAT_ARCHIVE, CONVERTER_IO | XNAT_ADMIN_PASSWORD, XNAT_SERVICE_PASSWORD, XNAT_DB_PASSWORD | HTTP 8080, **TCP `XNAT_PORT`** |
| `xnat-db` | block volume | XNAT_DB_PASSWORD | TCP 5432 |
| `xnat-nginx` | none | none | HTTP 80 |
| `orthanc` | block volume | ORTHANC_PASSWORD | HTTP 8042, **TCP 4242** |
| `dcm2niix` (job) | stages XNAT_ARCHIVE, CONVERTER_IO | none | none |
| `xnat-configure` (job) | none | XNAT_ADMIN_PASSWORD, XNAT_SERVICE_PASSWORD | none |
| `seed-orthanc` (job) | stage SEED (read-only) | ORTHANC_PASSWORD | none |
| `smoke-cstore` (job) | stage XNAT_ARCHIVE (read-only) | ORTHANC_PASSWORD, XNAT_SERVICE_PASSWORD | none |
| `reconvert` (job) | stages CONVERTER_IO, XNAT_ARCHIVE (read-only) | TRUST_INTERNAL_SERVICE_KEY, AES_KEY_BASE64, XNAT_SERVICE_PASSWORD | none |

Not on Snowflake: `omop-db` (replaced by native Snowflake tables) and Loki/Grafana/Alloy
(replaced by the account event table: three always-on containers cost about 44 credits a
month against roughly 0.02 credits per GB of event-table ingest).

### Why these shapes

**No public endpoints, and two internal TCP ones.** The only inbound path a trust needs
is DICOM. Retrieval is DQR: after XNAT issues C-FIND/C-MOVE, the PACS opens a *new*
association back to XNAT to C-STORE the studies. Without a reachable receiver the
queries succeed and every retrieval silently times out (FLIP#993), so `xnat-web` carries
a TCP endpoint on the kit's `XNAT_PORT`, which is also the port DQR matches the C-MOVE
destination against. Orthanc's 4242 is the mocked PACS's side of the same conversation.

**Service DNS is derived, never copied.** An SPCS service's DNS name comes from the
service, schema and database names, so Compose's `http://imaging-api:8000` resolves
nowhere. Every inter-service URL (`IMAGING_API_URL`, `DATA_ACCESS_API_URL`, `XNAT_URL`,
`PACS_HOST`, the XNAT JDBC URL) is built by `naming.Names.dns`.

**The fl-client gets the trust-internal key and nothing else.** Researcher training code
runs inside the fl-client as the same user as the client. A mapped `AES_KEY_BASE64` would
let it pass data-access-api's cohort-admin gate and rewrite or delete a project's approved
cohort (FLIP#857, `trust/AGENTS.md`). Its images mount is sliced to `net-<N>/` for the
same reason the Compose client's bind mount is: imaging-api's returned paths live under
`<IMAGES_DIR>/<NET_ID>/`, and the whole tree would be every other net's cached studies.

**Block volumes only for the two databases.** Everything else shares a file tree on a
stage, which survives a redeploy and can be read by more than one service. A Postgres
data directory is not that shape.

**No OMOP password anywhere.** `data-access-api` reads the SPCS-mounted OAuth session
token (mode `0600`) and connects as `FLIP_<CODE>_OMOP_READER`, a SELECT-only role with a
statement timeout. `OMOP_BACKEND=snowflake` plus the `SNOWFLAKE_*` variables are all this
directory sets; the backend seam itself is FLIP#13.

**Images by digest.** `mirror-images` pulls `linux/amd64` from GHCR, pushes into the
account repository and records the digest the registry returned. The renderer then pins
that digest: a tag in the account repository can be re-pushed, a digest cannot. SPCS runs
amd64 only, so the explicit platform matters on an arm64 workstation, where a tag pull
gets arm64 silently and the service fails to start with an exec-format error.

### The one-shot jobs

Four of the Compose and Helm targets have no equivalent in `make up`, because they are
things you *do* to a trust rather than services it runs. On SPCS each is a job service:
rendered like any other spec, never created, executed on demand.

```bash
make -C trust/deploy/snowflake xnat-configure  KIT=<CODE> HUB=<env>
make -C trust/deploy/snowflake seed-orthanc    KIT=<CODE> HUB=<env>
make -C trust/deploy/snowflake smoke-cstore    KIT=<CODE> HUB=<env>
make -C trust/deploy/snowflake reconvert-nifti KIT=<CODE> HUB=<env>
make -C trust/deploy/snowflake job-status      KIT=<CODE> HUB=<env> JOB=<name>
```

Each is `EXECUTE JOB SERVICE ... ASYNC = TRUE` with a `NAME`, run as the services role
with `CREATE SERVICE` granted for the length of the call and revoked in a `finally`.
`ASYNC` because a statement times out at 300 s and `configure-xnat.sh` alone outlives
that on a cold site; the `NAME` because `SYSTEM$GET_SERVICE_STATUS` and
`SYSTEM$GET_SERVICE_LOGS` are the only way to read what a job did, and an anonymous job
cannot be addressed once the call returns. All four take `DRY_RUN=true`.

| Job | Compose / Helm twin | What is different here |
|---|---|---|
| `xnat-configure` | `make -C trust/xnat xnat-configure`, the chart's `xnat-init` hook | Runs `configure-xnat.sh` unmodified from the xnat-web image. It deliberately does **not** run `configure-dcm2niix.sh`: there is no Container Service on SPCS, and registering the command would give XNAT a converter it can never launch |
| `seed-orthanc` | `make -C trust seed-orthanc` | The compose path streams the published DICOM from Hugging Face. A Snowflake trust has no egress and no host to download onto, so the corpus is PUT on the `SEED` stage once and this job uploads what it finds |
| `smoke-cstore` | `make -C trust/deploy/helm smoke-cstore` | There is no `exec` on SPCS, so the receiver's `received.log` cannot be read. Evidence is XNAT's own REST API first (a new session, then a new prearchive entry); the `XNAT_ARCHIVE` stage's file count alone is reported as INCONCLUSIVE (exit 2), because a concurrent DQR import looks identical |
| `reconvert` | `make -C trust reconvert-nifti` | imaging-api's own script with `CONVERTER_BACKEND=snowflake`, so it CALLs `APP.CONVERT_SCAN` rather than XNAT's bulk launcher, and registers the NIfTI in XNAT itself |

**The SEED stage layout** is this directory's contract, and the seeding job is the only
thing that reads it:

```
@FLIP_TRUST_<CODE>.STAGES.SEED/dicom/<project>/<accession>/<instance>.dcm
```

Put it there once, from a workstation that has the data:

```bash
snow stage copy ./dicom @FLIP_TRUST_GSTT.STAGES.SEED/dicom/ --recursive \
  --enable-templating NONE --connection flip-test
```

A re-run of `seed-orthanc` uploads the same instances and stores none of them again:
Orthanc dedupes on SOPInstanceUID. `SEED_PROJECTS` in the kit limits it to named
projects; unset, every project directory on the stage is seeded.

### DICOM to NIfTI conversion

There is no XNAT Container Service here: no Docker socket, no swarm, no Kubernetes
backend for the plugin to drive. `xnat-web` therefore runs with
`XNAT_CONTAINER_SERVICE_ENABLED=false`, and conversion goes through imaging-api instead,
gated by `CONVERTER_BACKEND=snowflake` (the SPCS spec sets it; Compose and Helm leave it
at `container-service` and are unchanged):

1. imaging-api CALLs `APP.CONVERT_SCAN(project, session, scan)`, an owner's-rights
   procedure owned by `FLIP_<CODE>_CONVERTER` (Terraform, FLIP#1581). It is a procedure
   because launching a job needs `CREATE SERVICE`, and a service's OAuth token carries
   the role that OWNS it: granting the services role that privilege would let the
   fl-client, which runs researcher code, start arbitrary containers.
2. It polls `APP.CONVERT_STATUS`. An empty status (`[]`) means the job exists and has not
   been scheduled yet, which is **pending**, not failed.
3. It reads the output from its own mount of the `CONVERTER_IO` stage, at
   `<project>/<session>/<scan>/`, and registers it in XNAT over REST as a `NIFTI`
   resource. That is the step the container service's output handler performed before.

`session` there is the XNAT session **label**, because the archive tree is
`<project>/arc001/<label>/SCANS/<scan>/DICOM` and that is what the procedure mounts. The
REST registration addresses the **experiment id**. The two are different things and both
appear on this path.

### Secret handling

`sync-kit` feeds every statement to `snow sql` on **stdin**. A secret value never appears
in a command line, in this script's output, or on disk:

* An argument is visible in `ps` to every user on the host, and is recorded by shell
  history and by most CI runners' command logging. It has leaked before the process has
  done anything.
* `--dry-run` prints the same SQL with every value replaced by `<redacted>`.
* `tests/test_sync_kit.py` drives the whole flow through a fake `snow` binary that records
  its `argv` and its stdin separately, and asserts the values are on stdin and nowhere
  else. The counterpart guard asserts they *do* reach stdin, so a sync that leaks nothing
  because it sent nothing is not a pass.

Secrets are created with `CREATE SECRET IF NOT EXISTS` and then `ALTER SECRET ... SET`,
never `CREATE OR REPLACE`: replacing a secret drops every grant on it, and the next
deploy's services fail to start on a privilege error about an object that plainly exists.
`sync-kit` also issues `GRANT READ ON SECRET ... TO ROLE FLIP_<CODE>_SVC`, because the
Terraform provider cannot grant on an object that does not exist yet and these objects
are created here, not there.

## Roles

Everything in this directory runs as **`FLIP_<CODE>_DEPLOY`**, the least-privilege
workload role, signed in as the `FLIP_<CODE>_DEPLOY_USER` `SERVICE` user with key-pair
auth, except the service creates in `make up`. `FLIP_<CODE>_ADMIN` is **retired**
(two-tier decision, 2026-10-20, epic FLIP#1589).

| Object | Owner | Granted |
| --- | --- | --- |
| Services (`CREATE OR ALTER SERVICE`) | `FLIP_<CODE>_SVC` | created as that role; the deploy role drops and suspends them through the role hierarchy |
| dcm2niix jobs | `FLIP_<CODE>_CONVERTER` | started only by `CALL APP.CONVERT_SCAN` (Terraform workload root) |
| Secrets (`sync-kit`) | `FLIP_<CODE>_DEPLOY` | `READ` to `FLIP_<CODE>_SVC` |
| Database, schemas, warehouse, pools, repository, stages | `FLIP_<CODE>_DEPLOY` | per the Terraform workload root |

**Why services are owned by the services role (FLIP#1578).** A service's OAuth token
(`/snowflake/session/token`) carries the role that owns the service, and SPCS refuses
`GRANT OWNERSHIP ON SERVICE`. Created by the deploy role, every container, the fl-client
running researcher code among them, would hold a token able to drop the trust database. So
`make up` (`spcs_sql.py apply-up`) grants `CREATE SERVICE` on `APP` to `FLIP_<CODE>_SVC`,
creates the services as that role, and revokes the grant in a `finally`: a failed spec, a
lost connection or Ctrl-C still revokes it. Verified on the test account: the token of a
service created this way is `FLIP_<CODE>_SVC`, and `CREATE SERVICE`, `EXECUTE JOB SERVICE`
and the event table are all denied to it.

That role holds exactly four account privileges, granted once by the break-glass
Terraform root: `CREATE DATABASE`, `CREATE COMPUTE POOL`, `EXECUTE TASK`,
`BIND SERVICE ENDPOINT`. It has no `CREATE ROLE`, no `CREATE USER`, no
`CREATE INTEGRATION`, no `CREATE NETWORK POLICY` and no `MANAGE GRANTS`. The grants this
directory issues all work through **ownership**: an owner may grant privileges on its own
objects without `MANAGE GRANTS`.

Attaching `FLIP_<CODE>_HUB_EAI` to a service is deliberately **not** done here: hub egress
is tracked by FLIP#1586, and the integration itself is created by the break-glass root.

A kit that carries no value for a secret leaves that secret untouched and says so. That
is not an error: a trust running no XNAT has no XNAT passwords, and nothing maps them.

### Cost control

SPCS bills each compute-pool node while it runs, and **a resource monitor does not cover
compute pools**. So `make suspend` suspends every service *and* every pool, pool
auto-suspend is set by the Terraform root, and a nightly suspend task is part of the
epic's deliverable. An idle trust that was never suspended is the expensive failure mode.

## Testing

```bash
make -C trust/deploy/snowflake test
```

No account, no credentials, no network. The guards assert, over rendered specs and a fake
`snow` binary: no public endpoint; no secret value in any rendered byte or in the deploy
SQL; the exact secret map per service; the fl-client's read-only kit, net-scoped images
mount and absent AES key; block volumes only for `xnat-db` and `orthanc`; resource
requests and limits on every container; `/health` probes on the three APIs; the DICOM TCP
endpoints; derived SPCS DNS rather than Compose names; and `CREATE OR ALTER` rather than
`CREATE OR REPLACE` (which would drop the databases' block volumes).

For the one-shot jobs they additionally assert: that each stays a job on the job pool and
exposes no endpoint; that its script is inlined, since a job service can fetch nothing;
that neither curl-driven job puts a credential in argv; that the reading jobs' volumes
carry read-only intent (a smoke test able to write the archive could manufacture its own
evidence); that `EXECUTE JOB SERVICE` is `ASYNC` and named, runs as the services role, and
always revokes `CREATE SERVICE`; and that imaging-api and the `reconvert` job agree on
every converter setting, since the job *is* imaging-api's code.

The imaging-api side of the conversion path has its own suite, with the Snowflake
connector mocked: `make -C trust/imaging-api local_test`
(`tests/services/test_snowflake_converter.py`,
`tests/scripts/test_reconvert_nifti_snowflake.py`).

CI runs them from `.github/workflows/test_snowflake_specs.yml`, path-gated like every
other service suite (`scripts/pr_paths_changed.py`).

## Assumptions pending spikes

These are decisions taken so the node shape could be written down. Each is marked in the
code and may change when its spike lands; nothing here should be read as verified against
a live account.

| Assumption | Spike | If it turns out otherwise |
|---|---|---|
| ~~XNAT runs non-root with its archive on a stage volume and its DICOM SCP on an internal TCP endpoint~~ | FLIP#1577 | **Verified live.** The image needed three fixes to get there (FLIP#1583): the entrypoint's database host is no longer the literal `xnat-db`, the rendered-config copy is no longer written to a root-owned directory, and `conf/Catalina/localhost` is pre-created |
| ~~XNAT needs no Container Service plugin: `dcm2niix` runs as an SPCS job service that imaging-api executes~~ | FLIP#1578 | **Verified live.** imaging-api CALLs `APP.CONVERT_SCAN`; see "DICOM to NIfTI conversion" above |
| Every image runs unmodified on SPCS (amd64, non-root, stage-volume throughput adequate) | FLIP#1579 | Resource figures, `uid`/`gid` on stage volumes, and possibly the images themselves |
| A stage volume can be sliced to a prefix (`@...IMAGES/net-1`), which is what carries the per-net isolation rule | FLIP#1579 | The fl-client needs one stage per net instead, and the Terraform root grows a stage per FL network |
| Stage read-only is enforced by granting READ and not WRITE (SPCS has no `readOnly` mount flag). The intent is recorded per volume in `manifest.json` | FLIP#1579 | A different mechanism, or an accepted residual risk documented in the runbook |
| Orthanc's entrypoint can compose `ORTHANC__REGISTERED_USERS` from an `ORTHANC_PASSWORD` environment variable | FLIP#1579 | The image gains a small entrypoint shim, or the password is mounted as a file via `directoryPath` |
| The resource requests and limits are starting points, not measurements | FLIP#1579, FLIP#1585 | Revised against the self-test's observed usage |
| The self-test is a placeholder: service status and container logs read through `SYSTEM$` functions | FLIP#1585 | Replaced by real assertions (a cohort query, an image pull, an FL round) |

The hub-connected path cannot be tested on a trial account at all: trials exclude
External Network Access, so there is no External Access Integration and no egress. That
is why the first acceptance gate (FLIP#1585) is a *hubless* self-test.

## See also

* [`trust/AGENTS.md`](../../AGENTS.md) - the trust stack, kit files, trust-internal auth
* [`trust/deploy/README.md`](../README.md) - the trust node in its three shapes
* [`trust/deploy/helm/README.md`](../helm/README.md) - the Kubernetes twin
* [Snowflake service specification reference](https://docs.snowflake.com/en/developer-guide/snowpark-container-services/specification-reference)
