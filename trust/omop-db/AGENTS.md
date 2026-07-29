# AGENTS.md — trust/omop-db

## What this directory is

Two halves of one pipeline (merged from the retired private `flip-omop-db` repo, FLIP#834):

1. **Image build source** for `ghcr.io/londonaicentre/omop-db`: `Dockerfile` on `postgres:17` bakes the
   `files/` init chain (create `omop` schema → OMOP CDM 5.4 DDL → primary keys → indices → read-only
   roles → vocabulary tables) plus the core OMOP vocabulary. FK **constraints are deliberately absent
   from init** (`files/OMOPCDM_postgresql_5.4_constraints.sql` is copied to `/flip/omop/constraints.sql`
   and applied via `make apply-constraints` only AFTER data load — loading after constraints fails).
2. **Consumer harness** for the dev trust stacks: `update_omop_data.sh` downloads ready-populated pgdata
   volumes (versioned by `.data_version`) from the public HF dataset `aicentreflip/trust-data` into
   `volumes/Trust_<N>/db_data`, which `trust/deploy/compose_trust.<env>.yml` mounts.

`compose.yml` here is the **standalone build/populate stack** (one empty DB per trust + opt-in pgadmin
profile; config from gitignored `.env.build`), NOT the runtime trust stack.

## Load-bearing facts

- **`.data_version` must not move**: its path is hardcoded in `deploy/providers/AWS/Makefile`, which
  passes the value on to Ansible (`-e omop_data_version=`); the Helm chart consumes it via the
  `OMOP_DATA_VERSION` env var in `generate_values.py`.
- **Vocabulary licensing**: the core vocab bundle — an OHDSI Athena export, 59 vocabularies incl.
  SNOMED CT, LOINC, Read, dm+d (roster + versions in README "The core vocabulary bundle") — is licensed
  material: `data/` is gitignored and must never be committed; there is **no CI image build** for this
  service. Acquisition paths (README "The core vocabulary bundle"): org members via
  `make fetch-vocab-core` from `s3://$(VOCAB_S3_BUCKET)/vocab/` (default `flipdev-aicentre`, org AWS
  needed); external users self-serve an equivalent export from OHDSI Athena under their own licences;
  `make fetch-vocab-core-from-image` is a transitional credential-free fallback extracting from the
  already-published public image (which already redistributes it — a pre-existing exposure recorded in
  FLIP#834; vocab-free image rebuild tracked in FLIP#842, pgdata-tarball posture in FLIP#843; this repo
  must not add a second redistribution channel). The DICOM vocab
  (byte-identical to DICOM2OMOP `files/OMOP CDM Staging/` @ upstream `1ef3354`, Apache 2.0, pickle
  converted to CSV) is freely redistributable and lives on the HF dataset.
- **Read-only roles are a security boundary**: `files/create_readonly_users.sql` creates
  `omop_readonly_base` + `data_analyst_reader` (SELECT-only, explicit REVOKEs) — the database half of
  data-access-api's SQL-injection defence-in-depth (`data_access_api/services/cohort.py`). The analyst
  password is NOT in the image — it is set at first init from `DATA_ACCESS_POSTGRES_PASSWORD` and lives
  in the pgdata volume; rotate via `ALTER ROLE` + kit update, or rebuild volumes with a new `.env.build`
  value (see CONTRIBUTING.md).
- **Canonical dataset + N-trust split** (`src/omop_db_tools/dataset.py`): mock rows are ONE dataset on
  HF (`omop-csv/<version>/`), each row tagged `source_trust`. Partition modes: `legacy` (default —
  reproduces the original two-trust membership; REQUIRED for data consistent with the published mock
  Orthanc PACS volumes, whose studies match each trust's accession IDs) and `modulo`
  (`person_id % N`, any trust count, needs regenerated imaging data). All tables carry `person_id`, so
  person-level partitioning preserves referential integrity.
- The populate scripts run on the **host** against published ports (`OMOP_DB_HOST` defaults to
  localhost); `populate` must run against freshly-initialised, constraint-free databases.

## Commands

```bash
make update-omop-data [TRUST=1|2]   # consumer path: sync pgdata volumes from HF
cp .env.build.example .env.build    # once, before any build-pipeline target
make build                          # fetch-vocab-core + docker build
make up-build / down-build          # the standalone per-trust build DBs
make populate [NUM_TRUSTS=N PARTITION=modulo]  # fetch dataset + load N trust slices (shipped stack is
                                               # two-trust; N>2 needs a compose service + port first)
make apply-constraints              # AFTER populate
make push [OMOP_DB_TAG=...]         # publish to GHCR (manual — no CI build)
make local_test                     # ruff + mypy + pytest tests/unit (no DB needed)
```

## Conventions

- uv project `omop-db-tools` (`src/omop_db_tools/` layout); registered in root `Makefile` `UV_PROJECTS`
  and the `uv-lock` pre-commit hooks. Tests live in `tests/unit/` only — anything touching a real
  Postgres belongs in `tests/integration/` (none yet).
- SQL identifiers interpolated into statements must pass `import_tables.validate_identifier`.
- The vocab/dataset bundles under `data/` and the build env (`.env.build`) are gitignored — keep it
  that way.
