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

# Orthanc (mock PACS)

> We use Orthanc as a mock PACS server to store and serve DICOM files for testing purposes. Orthanc is an open-source, lightweight DICOM server that provides a RESTful API for managing medical images. Read more about Orthanc in the [Orthanc documentation](https://www.orthanc-server.com/).

## Authentication (FLIP-PT-091)

HTTP basic auth is always enforced: the image sets `ORTHANC__AUTHENTICATION_ENABLED=true`, and its `flip-entrypoint.sh` refuses to start unless `ORTHANC__REGISTERED_USERS` holds at least one non-empty username/password pair with no blank username or password anywhere in the map (missing/empty values, a userless `{}`, and empty- or whitespace-only credentials are all rejected) — so the container can never boot as an unauthenticated PACS (Orthanc would otherwise fall back to the well-known `orthanc`/`orthanc` default user). The entrypoint also refuses to start if `ORTHANC__AUTHENTICATION_ENABLED` is overridden to anything other than `true`, so auth cannot be switched off from the outside either.

Orthanc username and password are set by `ORTHANC_USERNAME` and `ORTHANC_PASSWORD` in the per-trust kit file `trust/.env.<CODE>.<env>` — see the **Trust-local credentials** section of [.env.GSTT.development.example](../.env.GSTT.development.example). These are trust-local secrets; the hub never sees them. The dev templates default to `admin`/`admin` — acceptable for this mock PACS serving public test data, but change them in the kit for any shared deployment. On Kubernetes the user map comes from the `orthanc-registered-users` secret key instead (see `deploy/providers/kubernetes/README.md`).

Only humans consume these credentials (the Orthanc Explorer UI on the published `PACS_UI_PORT`, or via the XNAT nginx `/orthanc/` reverse proxy, which passes the browser's `Authorization` header through). The platform's data path is plain DICOM (DIMSE) on port 4242 — XNAT's DQR plugin does C-FIND/C-MOVE by AE title, which involves no HTTP credentials. The DICOM-Web plugin is not enabled: nothing in FLIP issues QIDO/WADO/STOW requests.

You'll need to populate Orthanc with DICOM files in order to test FLIP locally. We have prepared mock DICOM data for each of the 2 dev trusts (GSTT and KCH) as Orthanc storage volumes, published to the public Hugging Face dataset [`aicentreflip/trust-data`](https://huggingface.co/datasets/aicentreflip/trust-data). In order to set up the storage locally, these data volumes need to be downloaded/extracted. They are fetched anonymously over HTTPS — no AWS CLI or credentials required. This is handled automatically when bringing up the trust containers via `make up` / `make up-trusts` (from the repository root) or `make -C trust up-trust KIT=GSTT` / `make -C trust up-trust KIT=KCH` for a single trust, and similarly they will be updated locally when the desired version changes (note for devs: this is controlled by the `.data_version` file in this directory).

```sh
make update-orthanc-data           # both trusts (default)
make update-orthanc-data TRUST=1   # Trust_1 only
make update-orthanc-data TRUST=2   # Trust_2 only
```

## Seeding a running Orthanc with datasets (FLIP#1100)

The storage volume above is a fixed two-project, two-trust snapshot. To put a
chosen set of projects' studies into a **running** trust's PACS — the PACS half
of `make -C trust seed KIT=<CODE>` — seed it:

```sh
make -C trust seed-orthanc KIT=GSTT PROJECTS="spleen_project cxr_project"
make -C trust seed-orthanc KIT=KCH  PROJECTS="spleen_project" CLEAR=1     # replace existing studies first
make -C trust seed-orthanc KIT=GSTT PROJECTS="cxr_project" DRY_RUN=1      # resolve and count only
```

`seed_orthanc.py` is the DICOM twin of `omop_db_tools.import_tables`: it reads
the same published `omop-csv/<version>/<project>/image_occurrence.csv`, takes the
accession numbers whose `source_trust` is this trust's slot number, streams the
project's DICOM set (`dicom/<version>/<project>.tar.gz`, one archive per project,
`<accession>/*.dcm` inside) into `volumes/dicom/` once, and POSTs each matching
instance to `/instances` on `PACS_UI_PORT`. The studies that land are therefore
exactly the ones the trust's OMOP rows point at — by construction. It refuses to
upload anything if an accession in the OMOP slice has no directory in the
archive. Orthanc dedupes on `SOPInstanceUID` and never overwrites, so a re-run
is a no-op (`AlreadyStored`) and replacing instances needs `CLEAR=1`.

A seed writes `.seeded` inside the storage dir. On the next `.data_version`
bump, `update-orthanc-data` refuses to re-snapshot over a seeded volume unless
`FORCE=1`, before the ~1 GB download.

### Publishing a project's DICOM set

`publish_dicom.py` turns a generator's output (a `.zip` or a directory of
`*.dcm`, any layout — instances are grouped by their `AccessionNumber` tag) into
the archive the seeder expects, and refuses to produce one the seeder could not
later resolve completely: every accession must equal the published
`image_occurrence.csv` set both ways, every `StudyInstanceUID` and `PatientID`
must be published, and the per-trust split is reported.

```sh
uv run trust/orthanc/publish_dicom.py --project spleen_project --data-version 20260729 \
    --source dicom_output.zip --fill-empty-numbers --out dist/dicom/20260729/spleen_project.tar.gz
hf upload aicentreflip/trust-data dist/dicom/20260729/spleen_project.tar.gz \
    dicom/20260729/spleen_project.tar.gz --type dataset
```

`--fill-empty-numbers` sets a present-but-empty `AcquisitionNumber` /
`SeriesNumber` to 1 (the spleen generator's output has both empty on every CT
instance, which makes MONAI Deploy's loader drop the series — see
`docs/source/working-with-flip-apps/package-model-as-map.rst`). Absent tags stay
absent; populated ones are untouched. Published sets: `dicom/20260729/`
`spleen_project` (41 studies, 3,650 instances, filled) and `cxr_project` (8,332
studies). Both are the original generator outputs, verified against the
published OMOP — not extractions from the storage tarballs.

To cut a new storage tarball (the snapshot path, for EC2/k8s): seed a fresh
Orthanc with `seed-orthanc`, then `tar -C <storage dir> -cf trust<N>_orthanc_data_<version>.tar .`
— the DICOM sets are the source, the tarball is derived from them.
