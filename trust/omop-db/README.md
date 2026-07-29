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
make build                         # fetches the core vocab, then builds the image
```

> **Vocabulary licensing.** The core vocab bundle contains licensed OMOP
> vocabularies (SNOMED CT, LOINC, Read, ...) and is therefore **never tracked
> in git** and not fetchable in CI. `make fetch-vocab-core` extracts it from
> the already-published public image (no credentialed download, no new
> exposure). The DICOM vocabulary (NEMA PS3, converted via
> [DICOM2OMOP](https://github.com/paulnagy/DICOM2OMOP), Apache 2.0) is freely
> redistributable and fetched anonymously from the Hugging Face dataset.

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
