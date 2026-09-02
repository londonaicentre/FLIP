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

# Trust OMOP database

Postgres database containing OMOP-ified data. This directory is both the
**build source** for the `ghcr.io/londonaicentre/omop-db` image (OMOP CDM 5.4
schema with the MI-CDM imaging extension — `image_occurrence`,
`image_feature` — plus read-only role setup; imported from the retired private
`flip-omop-db` repo, FLIP#834) and the **consumer harness** that downloads
ready-populated data volumes for the dev trust stacks.

**Every published artifact is licence-clean (FLIP#842/#843)**: the image and
the pgdata tarballs are *vocab-free* — the licensed core vocabulary is
streamed into each running database as a one-time seeding step
(`files/load_core_vocab.sh`), from a source each environment is licensed to
use.

## Using the database (dev trust stacks)

We have prepared mock data for each of the 2 dev trusts (GSTT and KCH) as postgres data volumes, published to the public Hugging Face dataset [`aicentreflip/trust-data`](https://huggingface.co/datasets/aicentreflip/trust-data). In order to set up the database locally, these data volumes need to be downloaded/extracted. They are fetched anonymously over HTTPS — no AWS CLI or credentials required. This will be handled automatically when
creating the trust containers, and similarly they will be updated locally when the desired version changes (note for devs: this is controlled by `trust/.data_version` — a git tag on that dataset, one pin for the OMOP and Orthanc data together; see "Data versions" in `trust/README.md`).

```sh
make update-omop-data           # both trusts (default)
make update-omop-data TRUST=1   # Trust_1 only
make update-omop-data TRUST=2   # Trust_2 only
```

The OMOP database container is normally started as part of a full trust stack from the repository root:

```sh
make up-trusts                     # both trusts
make -C trust up-trust KIT=GSTT    # GSTT only
make -C trust up-trust KIT=KCH     # KCH only
```

The downloaded volumes are **vocab-free** (~11 MB each): after the stack is
up, load the core vocabulary into each trust database once (idempotent — safe
to re-run; needs the bundle, see "The core vocabulary bundle" below, and
`psql` on the host). Unlike the stack commands above, these run from this
directory — `make -C trust/omop-db …` from the repository root:

```sh
make -C trust/omop-db load-omop-vocab                    # Trust_1 (GSTT, port 5434)
make -C trust/omop-db load-omop-vocab OMOP_DB_PORT=5436  # Trust_2 (KCH)
```

Cohort queries that join `omop.concept` return nothing until this step has
run. On EC2 trusts the equivalent load is part of `seed-trust-data` (Ansible);
on Kubernetes it is the chart's `omop-vocab-load` post-install job.

To ask a database whether it has been loaded — without a bundle, and without
loading anything — run the script's probe mode. It exits 0 only when every
vocabulary table already holds core rows, which is how the Kubernetes hook
decides whether it needs to download the bundle at all:

```sh
cd trust/omop-db && OMOP_DB_PORT=5434 OMOP_POSTGRES_USER=… OMOP_POSTGRES_PASSWORD=… \
  OMOP_POSTGRES_DB=… ./files/load_core_vocab.sh --check
```

### Seeding a running trust with datasets (FLIP#1100)

The snapshot above is a fixed two-project, two-trust cut. To load a chosen set of
projects into a **running** trust — a different set, a third trust, a dataset that
has no snapshot — seed it. Same lifecycle as the vocab load: one-time, after the
stack is up, idempotent, and it persists in the bind-mounted volume until a
`.data_version` bump. Run from `trust/` with the kit, which supplies the slot
number, the port and the credentials:

```sh
make -C trust seed-omop KIT=GSTT PROJECTS="spleen_project cxr_project"   # OMOP rows only
make -C trust seed KIT=GSTT PROJECTS="spleen_project cxr_project"        # + this trust's DICOMs into Orthanc
make -C trust seed-trusts PROJECTS="spleen_project cxr_project"          # both dev trusts
```

What it does: fetches the canonical CSVs at the pinned data version
(`omop-csv/<project>/` at the tag in `trust/.data_version`; `HF_TRUST_DATA_REVISION`
overrides), selects this trust's rows by their
`source_trust` column, deletes the listed projects' existing rows (by their own
`person_id`s — Synthea EHR rows and other projects are untouched) and loads the
slice, one transaction per project, into the constrained, vocab-loaded database
a running trust is. `seed-orthanc` (see `trust/orthanc/README.md`) puts the same
trust's studies into its PACS, selected by the same column, so OMOP and PACS
agree by construction rather than by keeping two snapshots in lockstep.

A seed writes a `.seeded` marker beside the trust's `db_data`. On the next
`.data_version` bump, `update-omop-data` refuses to re-snapshot over a seeded
volume — that would discard the seed *and* the vocabulary load — unless
`FORCE=1`; then re-run `load-omop-vocab` and `seed`.

For database-only debugging (without the rest of the trust stack), `make -C trust/omop-db up-test-omop-trust1` will start just the first dev trust's OMOP container.

Bringing the container up should not run any initialization scripts — the data volume already contains a populated database.

## Building the image

The image bakes the schema init chain (`files/`: schema DDL → primary keys →
indices → read-only users) plus the seed-time helpers
(`load_core_vocab.sh`, `constraints.sql`) into `postgres:17` — **no
vocabulary, no data**, so the build needs no credentials and is published by
CI (`docker_build_omop_db.yml`, test-gated like the other services). FK
constraints are deliberately **not** applied at init — data must load first
(see `apply-constraints` below).

```sh
cp .env.build.example .env.build   # local build credentials (gitignored)
make build                         # plain docker build — no data inputs
```

### The core vocabulary bundle

`vocab_aicentre_core_20240916` is an [OHDSI Athena](https://athena.ohdsi.org/)
vocabulary export (snapshot `v5.0 30-AUG-24`): nine tab-separated files
(~600 MB as the distributed zip, ~3.6 GB unpacked — so a loader that unpacks
beside the zip needs ~4.2 GB of scratch space, which is what sizes the
Kubernetes vocab-load Job's work dir — `CONCEPT.csv` 819 MB, `CONCEPT_RELATIONSHIP.csv` 2.0 GB,
`CONCEPT_ANCESTOR.csv` 704 MB, `CONCEPT_SYNONYM.csv` 139 MB,
`DRUG_STRENGTH.csv` 159 MB, plus the small `CONCEPT_CLASS` / `DOMAIN` /
`RELATIONSHIP` / `VOCABULARY` tables) that
`load_core_vocab.sh` streams into the `omop` schema at seed time. It carries
**59 vocabularies** — the load-bearing ones for
FLIP's cohort queries and the licensing-relevant ones are:

| Vocabulary | Version in bundle | Licensing |
|---|---|---|
| SNOMED CT | 2024-02 Int / 2024-03 US / 2024-04 UK editions | Affiliate licence (SNOMED International / NHS) |
| LOINC | 2.77 | Regenstrief terms of use |
| Read v2 | NHS READV2 21.0.0 | NHS TRUD licence |
| dm+d | 2023-05-22 | NHS |
| ICD-10 | WHO 2021 release | WHO |
| ICD-9-CM / ICD-10-CM / ICD-10-PCS | v32 / FY2025 / 2024 | Public domain (US) |
| RxNorm / RxNorm Ext / NDC | 20240506 / 20240701 / 20240825 | UMLS terms (RxNorm) |
| OMOP structural vocabularies (Domain, Concept Class, Type Concept, Visit, ...) | — | Apache 2.0 (OHDSI) |

Because of the licensed entries the bundle is **never tracked in git**, never
part of the published image, and not fetchable in CI. Two ways to obtain it
(not needed to build the image, but needed both to populate fresh datasets and
to run the one-time `make load-omop-vocab` seeding of any stack — see
"Using the database (dev trust stacks)" above; a stack whose vocabulary was never loaded
starts cleanly but returns nothing from cohort queries that join
`omop.concept`):

1. **FLIP developers (org AWS access)** — `make fetch-vocab-core`: downloads
   `s3://flipdev-aicentre/vocab/vocab_aicentre_core_20240916.zip` (override
   the bucket with `VOCAB_S3_BUCKET=`) and unpacks it into `data/` — the same
   source and technique the private repo used. Each deploy environment reads
   from its **own** bucket (`AICENTRE_BUCKET_NAME`; no cross-account access) —
   when staging the zip in an env bucket, upload it with `--sse AES256`: the
   trust EC2 role has no `kms:Decrypt` on the buckets' default KMS keys.
2. **Anyone, under their own licences** — build an equivalent bundle from
   [OHDSI Athena](https://athena.ohdsi.org/): request an export containing the
   vocabularies in the roster above (SNOMED CT and LOINC require accepting
   their licences on Athena; the UK SNOMED / Read / dm+d editions come via
   [NHS TRUD](https://isd.digital.nhs.uk/)), then place the export's CSV files
   at `data/vocab_aicentre_core_20240916/`. Concept coverage may differ
   slightly from the org snapshot depending on release dates.

### The DICOM vocabulary bundle

`vocab_dicom_paulnagy_20260109` (loaded at populate time by
`load_dicom_vocab.py`) is an exact, byte-for-byte copy of four files from the
Apache-2.0 [DICOM2OMOP](https://github.com/paulnagy/DICOM2OMOP) project's
`files/OMOP CDM Staging/` directory as of upstream commit `1ef3354`
(2026-01-08, "Update notebook with new numbering and regenerate tables"):
`omop_table_staging_v5.csv`, `cs_values_maps_to.csv`,
`cs_values_maps_to_value.csv`, and `part3_to_part16_relationship_via_CID` —
the last converted from the upstream pickle to CSV when the bundle was
published to the Hugging Face dataset, so the loader deserialises no pickles.
It is NEMA PS3-derived and freely redistributable; `make fetch-vocab-dicom`
fetches it anonymously.

## Populating (the canonical dataset and N-trust splitting)

The synthetic mock rows live as **one canonical CSV dataset** on the public
Hugging Face dataset at `omop-csv/<project>/` — one copy, read at the data-version
tag pinned in `trust/.data_version` (`HF_TRUST_DATA_REVISION` overrides, e.g.
`main` for tables uploaded but not tagged yet). Every row carries a `source_trust`
column — the trust it belongs to, decided by the dataset's own generator — and
standing up N trusts is a deterministic split (`src/omop_db_tools/dataset.py`):

- `source_trust` (default): partition by that column. The partition is *data*:
  explicit, inspectable, versioned with the dataset, and the per-project DICOM
  sets are keyed on it too (`trust/orthanc/seed_orthanc.py`), so a trust's OMOP
  rows and the studies in its PACS agree by construction. Any trust count the
  column carries. `legacy` is still accepted as an alias — the mode's old name,
  from when it existed only to reproduce the two-trust cut frozen in the
  published Orthanc tarballs.
- `modulo`: partition by `person_id % NUM_TRUSTS` — for a dataset that carries
  no partition column, and only then: it ignores whatever the generator decided.

```sh
uv sync
make up-build                        # builds if needed, then starts the two build DBs
make populate                        # core vocab + DICOM vocab + each trust's dataset slice
make apply-constraints               # FK constraints go on AFTER the load
```

Populating runs from the host and needs `psql`/`pg_isready`
(postgresql-client). The shipped build stack is **two-trust**: `NUM_TRUSTS` /
`PARTITION` thread through to the split tooling, but standing up more than two
trusts additionally needs an `omop-db-trust<N>` service in `compose.yml` and
an `OMOP_DB_PORT_TRUST_<N>` in `.env.build` — `make populate NUM_TRUSTS=3
PARTITION=modulo` fails fast until they exist (and `modulo` implies
regenerating the matching imaging data). `populate` and `seed-omop` are the
same loader (`omop_db_tools.import_tables`): `populate` passes `--clean all`
(empty build databases), `seed-omop` uses the default `--clean projects`.

### Publishing new pgdata tarballs

The published tarballs must be **vocab-free** (they are public): run the
pipeline with the core-vocabulary step skipped and WITHOUT `apply-constraints`
(the FKs reference the absent vocab tables — they are applied at seed time by
the vocab load instead):

```sh
make up-build && make populate CORE_VOCAB=0
make export-pgdata                   # dist/trust<N>_pgdata.tar (no version in the name)
```

Then publish them as part of a new data version — one commit on the dataset
that replaces `trust<N>/trust<N>_pgdata.tar`, plus a tag — and bump
`trust/.data_version` to that tag:

```sh
make -C trust publish-trust-data VERSION=20261001 PGDATA="omop-db/dist/trust1_pgdata.tar omop-db/dist/trust2_pgdata.tar"
```

The DICOM vocabulary and the synthetic cohort stay in the tarball (both freely
redistributable); the archives are ~11 MB. The previous version's bytes remain
at the previous tag; nothing is copied or renamed.

To publish the image manually (CI normally does this): `make push`
(GHCR write access required; `OMOP_DB_TAG` overrides the tag, and the target
asks for confirmation — CI publishes `:latest` only from `main`, so an
unqualified push repoints the "newest release" pointer at a local build).

The canonical dataset is regenerated from per-trust CSV exports with
`uv run python -m omop_db_tools.dataset build --trust-dirs <dir1> <dir2> --dest <out>`.

## Further Reading

- [Trust deployment overview](../README.md)
- [Contributing & Development Guide](../../CONTRIBUTING.md)
