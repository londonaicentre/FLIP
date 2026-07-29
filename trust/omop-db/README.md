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
creating the trust containers, and similarly they will be updated locally when the desired version changes (note for devs: this is controlled by the `.data_version` file in this directory).

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
`psql` on the host):

```sh
make load-omop-vocab                    # Trust_1 (GSTT, port 5434)
make load-omop-vocab OMOP_DB_PORT=5436  # Trust_2 (KCH)
```

Cohort queries that join `omop.concept` return nothing until this step has
run. On EC2 trusts the equivalent load is part of `seed-trust-data` (Ansible);
on Kubernetes it is the chart's `omop-vocab-load` post-install job.

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
(~3.6 GB unpacked — `CONCEPT.csv` 819 MB, `CONCEPT_RELATIONSHIP.csv` 2.0 GB,
`CONCEPT_ANCESTOR.csv` 704 MB, `CONCEPT_SYNONYM.csv` 139 MB,
`DRUG_STRENGTH.csv` 159 MB, plus the small `CONCEPT_CLASS` / `DOMAIN` /
`RELATIONSHIP` / `VOCABULARY` tables) that
`60_populate_vocabulary_tables.sql` COPYs into the `omop` schema at first
container init. It carries **59 vocabularies** — the load-bearing ones for
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
(needed only for populating fresh datasets — not for building the image or
running the trust stacks):

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
Hugging Face dataset under `omop-csv/<version>/` (version pinned by
`OMOP_CSV_DATA_VERSION` in the Makefile). Every row carries a `source_trust`
provenance column, and standing up N trusts is a deterministic split
(`src/omop_db_tools/dataset.py`):

- `legacy` (default): partition by `source_trust` — reproduces the original
  two-trust membership exactly, keeping each trust's OMOP accession IDs
  consistent with that trust's published mock PACS (Orthanc) data.
- `modulo`: partition by `person_id % NUM_TRUSTS` — any trust count, for fresh
  stand-ups where the imaging data is regenerated to match.

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
regenerating the matching imaging data).

### Publishing new pgdata tarballs

The published tarballs must be **vocab-free** (they are public): run the
pipeline with the core-vocabulary step skipped and WITHOUT `apply-constraints`
(the FKs reference the absent vocab tables — they are applied at seed time by
the vocab load instead):

```sh
make up-build && make populate CORE_VOCAB=0
make export-pgdata                   # dist/trust<N>_pgdata_<.data_version>.tar
```

Upload each archive under `trust<N>/` in the Hugging Face dataset and bump
`.data_version`. The DICOM vocabulary and the synthetic cohort stay in the
tarball (both freely redistributable); the archives are ~11 MB.

To publish the image manually (CI normally does this): `make push`
(GHCR write access required; `OMOP_DB_TAG` overrides the tag, and the target
asks for confirmation — the trust stacks resolve `:latest` by default).

The canonical dataset is regenerated from per-trust CSV exports with
`uv run python -m omop_db_tools.dataset build --trust-dirs <dir1> <dir2> --dest <out>`.

## Further Reading

- [Trust deployment overview](../README.md)
- [Contributing & Development Guide](../../CONTRIBUTING.md)
