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
`image_feature` — plus read-only role setup and vocabulary init; imported from
the retired private `flip-omop-db` repo, FLIP#834) and the **consumer harness**
that downloads ready-populated data volumes for the dev trust stacks.

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

For database-only debugging (without the rest of the trust stack), `make -C trust/omop-db up-test-omop-trust1` will start just the first dev trust's OMOP container.

Bringing the container up should not run any initialization scripts — the data volume already contains a populated database.

## Building the image

The image bakes the schema init chain (`files/`: schema DDL → primary keys →
indices → read-only users → vocabulary tables) and the core OMOP vocabulary
into `postgres:17`. FK constraints are deliberately **not** applied at init —
data must load first (see `apply-constraints` below).

```sh
cp .env.build.example .env.build   # local build credentials (gitignored)
make build                         # fetches the core vocab (S3), then builds the image
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

Because of the licensed entries the bundle is **never tracked in git** and not
fetchable in CI. `make fetch-vocab-core` downloads it from the org S3 bucket
(`s3://flipdev-aicentre/vocab/`, override with `VOCAB_S3_BUCKET=`; needs AWS
credentials for that account — the same source and technique the private repo
used). Without bucket access, `make fetch-vocab-core-from-image` extracts the
identical bundle from the already-published public image instead (no
credentials, no new exposure).

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
make populate                        # fetch DICOM vocab + dataset, load each trust's slice
make apply-constraints               # FK constraints go on AFTER the load
```

Populating runs from the host and needs `pg_isready` (postgresql-client). The
shipped build stack is **two-trust**: `NUM_TRUSTS` / `PARTITION` thread through
to the split tooling, but standing up more than two trusts additionally needs
an `omop-db-trust<N>` service in `compose.yml` and an `OMOP_DB_PORT_TRUST_<N>`
in `.env.build` — `make populate NUM_TRUSTS=3 PARTITION=modulo` fails fast
until they exist (and `modulo` implies regenerating the matching imaging data).

The populated volumes land in `volumes/Trust_<N>/db_data` — the same trees the
dev trust stack mounts. To publish a new pgdata version to Hugging Face, tar
the *contents* of each populated volume
(`tar -czf trust<N>_pgdata_<version>.tar -C volumes/Trust_<N>/db_data .` — the
archive root must be the db_data contents, not a wrapping directory) and upload
it under `trust<N>/` in the dataset, then bump `.data_version`.

To publish the image itself: `make push` (GHCR write access required;
`OMOP_DB_TAG` overrides the tag, and the target asks for confirmation — the
trust stacks resolve `:latest` by default).

The canonical dataset is regenerated from per-trust CSV exports with
`uv run python -m omop_db_tools.dataset build --trust-dirs <dir1> <dir2> --dest <out>`.

## Further Reading

- [Trust deployment overview](../README.md)
- [Contributing & Development Guide](../../CONTRIBUTING.md)
