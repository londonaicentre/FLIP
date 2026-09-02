# CLAUDE.md — trust/omop-db

## What this directory is

Two halves of one pipeline (merged from the retired private `flip-omop-db` repo, FLIP#834):

1. **Image build source** for `ghcr.io/londonaicentre/omop-db`: `Dockerfile` on `postgres:17` bakes the
   `files/` init chain (create `omop` schema → OMOP CDM 5.4 DDL → primary keys → indices → read-only
   roles) plus the seed-time helpers (`load_core_vocab.sh`, `constraints.sql`, and `unzip` — the
   k8s vocab-load Job unpacks the bundle with the image's own copy so it installs nothing at
   run time, keeping S3 the only host it must reach). The image is
   **vocab-free** (FLIP#842) — nothing licensed in any layer — so it is published by CI
   (`docker_build_omop_db.yml`, gated on the "Trust - OMOP DB CI" test workflow like the other
   services). FK **constraints are deliberately absent from init** — they are applied only AFTER data
   load (loading after constraints fails).
2. **Consumer harness** for the dev trust stacks: `update_omop_data.sh` downloads ready-populated,
   **vocab-free** pgdata volumes (~11 MB each, fetched at the tag pinned by `trust/.data_version`) from the public HF dataset
   `aicentreflip/trust-data` into `volumes/Trust_<N>/db_data`, which
   `trust/deploy/compose_trust.<env>.yml` mounts.

`compose.yml` here is the **standalone build/populate stack** (one empty DB per trust + opt-in pgadmin
profile; config from gitignored `.env.build`), NOT the runtime trust stack.

## The vocabulary seeding model (FLIP#842/#843)

No published artifact (image, pgdata tarball, HF dataset) carries the licensed core vocabulary. Every
environment loads it ONCE into the running database via `files/load_core_vocab.sh` (client-side
`COPY FROM STDIN` over TCP — no mounts, no server-side files; idempotent via core-aware guards that
tolerate the DICOM vocab already present in the tarballs):

- **Dev**: `make load-omop-vocab [OMOP_DB_PORT=5436]` (after `update-omop-data` + stack up). Cohort
  queries joining `omop.concept` return nothing until this runs.
- **EC2**: the "load OMOP core vocabulary on Trust EC2" Ansible play (part of `seed-trust-data`;
  throwaway container on loopback port 15499; kit credentials passed by the AWS Makefile).
- **Kubernetes**: the chart's `omop-vocab-load` post-install/post-upgrade hook Job
  (`omopDb.vocabLoad` values; bundle from S3, loader + constraints from the image). Its first
  stage runs `load_core_vocab.sh --check` and skips the multi-GB fetch when the database is
  already loaded — the hook sits on the critical path of every `helm upgrade`, so the fetch
  must stay conditional. The loader still runs (constraints).

## Load-bearing facts

- **`trust/.data_version` is THE pin and must not move**: one value for the OMOP and Orthanc mock
  data together, a git tag on `aicentreflip/trust-data` (the dataset holds one copy of every artefact
  at an unversioned path; consumers fetch `resolve/<tag>/<path>`). Its path is hardcoded in
  `deploy/providers/AWS/Makefile` (→ Ansible `-e trust_data_version=`), both update scripts, this
  Makefile, `seed_orthanc.py` and the spleen uploader; the Helm chart carries the same value as
  `trustData.version` (`TRUST_DATA_VERSION` in `generate_values.py`). Publish with
  `make -C trust publish-trust-data VERSION=<tag> …`, then bump the pin. Never upload a versioned
  filename or `omop-csv/<v>/` directory again.
- **Vocabulary licensing**: the core vocab bundle — an OHDSI Athena export, 59 vocabularies incl.
  SNOMED CT, LOINC, Read, dm+d (roster + versions in README "The core vocabulary bundle") — is licensed
  material: `data/` is gitignored and must never be committed or published. Acquisition: org members via
  `make fetch-vocab-core` from `s3://$(VOCAB_S3_BUCKET)/vocab/` (default `flipdev-aicentre`, org AWS
  needed); external users self-serve an equivalent export from OHDSI Athena under their own licences.
  The DICOM vocab (byte-identical to DICOM2OMOP `files/OMOP CDM Staging/` @ upstream `1ef3354`, Apache
  2.0, pickle converted to CSV) is freely redistributable: it lives on the HF dataset and stays inside
  the published tarballs.
- **Read-only roles are a security boundary**: `files/create_readonly_users.sql` creates
  `omop_readonly_base` + `data_analyst_reader` (SELECT-only, explicit REVOKEs) — the database half of
  data-access-api's SQL-injection defence-in-depth (`data_access_api/services/cohort.py`). The analyst
  password is NOT in the image — it is set at first init from `DATA_ACCESS_POSTGRES_PASSWORD` and lives
  in the pgdata volume; rotate via `ALTER ROLE` + kit update, or rebuild volumes with a new `.env.build`
  value (see CONTRIBUTING.md).
- **Canonical dataset + N-trust split** (`src/omop_db_tools/dataset.py`): mock rows are ONE dataset on
  HF (`omop-csv/<project>/`, read at the pinned tag), each row carrying `source_trust` — the trust it belongs to, decided by
  the dataset's generator. Partition modes: `source_trust` (default; `legacy` is an accepted alias —
  the old name from when the mode existed only to match the two-trust cut frozen in the Orthanc
  tarballs) and `modulo` (`person_id % N`, only for a dataset with no partition column). The
  partition is *data*, and the per-project DICOM sets are keyed on the same column, so OMOP and PACS
  agree by construction (#1100). All tables carry `person_id`, so person-level partitioning preserves
  referential integrity. `CANONICAL_TABLES` is in FK-safe order — loads as listed, cleans reversed —
  because the seed path targets a constrained, vocab-loaded running trust.
- **Seeding a running trust** (#1100): `make -C trust seed KIT=<CODE> PROJECTS="…"` loads the listed
  projects' rows (`import_tables --clean projects`, by the projects' own `person_id`s — Synthea rows
  and other projects untouched, one transaction per project) and their DICOMs (`orthanc/seed_orthanc.py`)
  into that trust, selected by `source_trust == FL_KIT_SLOT_NUMBER`. Same lifecycle as
  `load-omop-vocab`: one-time post-snapshot, idempotent, persists in the bind-mounted volume. A
  `.seeded` marker beside `db_data` makes `update_omop_data.sh` refuse to re-snapshot on a bump
  without `FORCE=1`. `populate` is the same loader with `--clean all`.
- The populate scripts run on the **host** against published ports (`OMOP_DB_HOST` defaults to
  localhost) and need postgresql-client (`psql`/`pg_isready`).

## Commands

```bash
make update-omop-data [TRUST=1|2]   # consumer path: sync vocab-free pgdata volumes from HF
make load-omop-vocab [OMOP_DB_PORT=5436]  # seed the licensed vocab + constraints into a running trust DB
cp .env.build.example .env.build    # once, before any build-pipeline target
make build                          # plain docker build — no data inputs, no credentials
make up-build / down-build          # the standalone per-trust build DBs
make populate [NUM_TRUSTS=N PARTITION=modulo]  # core vocab + DICOM vocab + N trust slices (shipped
                                               # stack is two-trust; N>2 needs a compose service + port)
make populate CORE_VOCAB=0          # vocab-free flavour for publishable tarballs (skip apply-constraints!)
make seed-omop TRUST_INDEX=2 OMOP_DB_PORT=5436 PROJECTS="…"  # seed a RUNNING trust; normally via `make -C trust seed KIT=…`
make export-pgdata                  # tar each volume -> dist/trust<N>_pgdata.tar (version = the tag publish-trust-data puts on it)
make apply-constraints              # AFTER a full populate
make push [OMOP_DB_TAG=...]         # manual publish escape hatch (CI publishes normally); confirms first
make local_test                     # ruff + mypy + pytest tests/unit (no DB needed)
```

## Conventions

- uv project `omop-db-tools` (`src/omop_db_tools/` layout); registered in root `Makefile` `UV_PROJECTS`
  and the `uv-lock` pre-commit hooks. Tests live in `tests/unit/` only — anything touching a real
  Postgres belongs in `tests/integration/` (none yet).
- SQL identifiers interpolated into statements must pass `import_tables.validate_identifier`.
- The vocab/dataset bundles under `data/`, exported tarballs under `dist/`, and the build env
  (`.env.build`) are gitignored — keep it that way.
