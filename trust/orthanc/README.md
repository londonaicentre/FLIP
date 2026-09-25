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

Orthanc username and password are set by `ORTHANC_USERNAME` and `ORTHANC_PASSWORD` in the per-trust kit file `trust/.env.<CODE>.<env>` — see the **Trust-local credentials** section of [.env.GSTT.development.example](../.env.GSTT.development.example). These are trust-local secrets; the hub never sees them. The dev templates default to `admin`/`admin` — acceptable for this mock PACS serving public test data, but change them in the kit for any shared deployment. On Kubernetes the user map comes from the `orthanc-registered-users` secret key instead (see `trust/deploy/helm/README.md`).

Only humans consume these credentials (the Orthanc Explorer UI on the published `PACS_UI_PORT`, or via the XNAT nginx `/orthanc/` reverse proxy, which passes the browser's `Authorization` header through). The platform's data path is plain DICOM (DIMSE) on port 4242 — XNAT's DQR plugin does C-FIND/C-MOVE by AE title, which involves no HTTP credentials. The DICOM-Web plugin is not enabled: nothing in FLIP issues QIDO/WADO/STOW requests.

You'll need to populate Orthanc with DICOM files in order to test FLIP locally. The mock DICOM is the
per-project sets published to the public Hugging Face dataset
[`aicentreflip/trust-data`](https://huggingface.co/datasets/aicentreflip/trust-data)
(`dicom/<project>.tar.gz`), fetched anonymously over HTTPS — no AWS CLI or credentials required.
Bringing a trust up (`make up-trusts` from the repository root, or `make -C trust up-trust KIT=GSTT`
for one) starts Orthanc on an empty, 999-owned storage dir and then seeds this trust's slice of the
default projects into it (below); the storage persists, so a later `up` uploads nothing. The version
is `trust/.data_version`, a git tag on that dataset.

### Other Make targets

| Target | What it does |
| --- | --- |
| `make build` | Build the `orthanc` image from this directory's Dockerfile. |
| `make up-trust-1` | Run Trust_1's Orthanc in the foreground on `deploy_trust-network-1`, using the ports and credentials from `../.env.GSTT.development.example`. |
| `make down` | Stop and remove that container (`--remove-orphans`). |
| `make shell` | Throwaway `/bin/bash` in the Orthanc image (`run --rm`) — for poking at the image, not at a running server. |
| `make test` | Pytest for `seed_orthanc.py` and `publish_dicom.py` plus `ruff check`/`format --check`. Pure Python, no Orthanc needed. |

These read defaults from the tracked kit example `../.env.GSTT.development.example`, so they always
address the **Trust_1** slot; drive any other trust through `make -C trust up-trust KIT=<CODE>`.

## Seeding a running Orthanc with datasets (FLIP#1100)

Seeding is how Orthanc gets its studies — at bring-up, and to put a
chosen set of projects' studies into a **running** trust's PACS — the PACS half
of `make -C trust seed KIT=<CODE>` — seed it:

```sh
make -C trust seed-orthanc KIT=GSTT PROJECTS="cxr_project"
make -C trust seed-orthanc KIT=KCH  PROJECTS="cxr_project" CLEAR=1        # replace existing studies first
make -C trust seed-orthanc KIT=GSTT PROJECTS="cxr_project" DRY_RUN=1      # resolve and count only
make -C fl-tutorials seed-spleen KIT=GSTT                                 # spleen / brain_mri: from a regenerated local tree (below)
```

`seed_orthanc.py` is the DICOM twin of `omop_db_tools.import_tables`: it reads
the same published `omop-csv/<project>/image_occurrence.csv` at the pinned data
version (the tag in `trust/.data_version`; `--revision` / `HF_TRUST_DATA_REVISION`
override), takes the accession numbers whose `source_trust` is this trust's slot
number, streams the project's DICOM set (`dicom/<project>.tar.gz` at that tag —
one archive per project, one copy, `<accession>/*.dcm` inside) into
`volumes/dicom/<revision>/` once, and POSTs each matching
instance to `/instances` on `PACS_UI_PORT`. The studies that land are therefore
exactly the ones the trust's OMOP rows point at — by construction. It refuses to
upload anything if an accession in the OMOP slice has no directory in the
archive. Orthanc dedupes on `SOPInstanceUID` and never overwrites, so a re-run
is a no-op (`AlreadyStored`) and replacing instances needs `CLEAR=1`.

A seed writes a marker beside the storage dir (`<parent>/.<storage dir>.seeded`, the
dir itself being owned by Orthanc's uid) recording projects, partition and data
version; `make -C trust ensure-seeded` compares it to decide whether to re-seed,
and on a `.data_version` bump re-runs the seed with `CLEAR=1` for the listed projects.

### Publishing a project's DICOM set

`publish_dicom.py` turns a generator's output (a `.zip` or a directory of
`*.dcm`, any layout — instances are grouped by their `AccessionNumber` tag) into
the archive the seeder expects, and refuses to produce one the seeder could not
later resolve completely: every accession must equal the published
`image_occurrence.csv` set both ways, every `StudyInstanceUID` and `PatientID`
must be published, and the per-trust split is reported.

```sh
uv run trust/orthanc/publish_dicom.py --project spleen_project --revision 20260729 \
    --source dicom_output.zip --fill-empty-numbers --out dist/dicom/spleen_project.tar.gz
make -C trust publish-trust-data VERSION=20261001 DICOM=orthanc/dist/dicom/spleen_project.tar.gz
```

`--revision` names the dataset revision whose `omop-csv/<project>` tables the
source is checked against: an existing tag, or `main` right after uploading new
tables and before tagging them together with the archive. The archive name
carries no version — `publish-trust-data` puts it on the dataset in one commit
and tags that commit, and `trust/.data_version` is then bumped to the tag.

`--fill-empty-numbers` sets a present-but-empty `AcquisitionNumber` /
`SeriesNumber` to 1 (the plastimatch-era spleen generator's output had both empty
on every CT instance, which makes MONAI Deploy's loader drop the series — see
`docs/source/working-with-flip-apps/package-model-as-map.rst`; the shared writer
that replaced it stamps both). Absent tags stay absent; populated ones are
untouched. Published sets (`dicom/`): `cxr_project` (8,332 studies, at every tag
from `20260729` on) and `prostate_project`. `spleen_project`'s set (41 studies,
3,650 instances, filled) is on the tags up to the FLIP#1221 cut only: from then on
spleen — like `brain_mri_project` — regenerates its DICOMs locally from the public
MSD archive and publishes only its tables, so there is nothing to package. All
published sets are the original generator outputs, verified against the published
OMOP. They are what a bring-up seeds from — every trust, dev, on-prem, EC2 or
Kubernetes, seeds its Orthanc from the dataset (FLIP#1187) — which is why the
default `PROJECTS` is the published-set list and a locally regenerated project is
seeded explicitly, below.

### Seeding a project whose DICOMs are not on the dataset

`seed_orthanc.py --source <dicom tree> --tables-dir <canonical tables>` (both
together; `make seed-orthanc … DICOM_SOURCE= TABLES_DIR=`) reads a regenerated tree
in any layout — instances are grouped by their `AccessionNumber` tag, as
`publish_dicom.py` reads them — and selects this trust's slice from the local
`image_occurrence.csv` by the same `source_trust` column, with the same
zero-mismatch guard. The `.seeded` marker then records `source=<dir>` instead of a
data version. `make -C fl-tutorials seed-spleen KIT=<CODE>` and `seed-brain-mri`
wrap this together with the OMOP half (`seed-omop … CANONICAL_DIR=`), so a project
can be proven on a running trust before its data version is tagged.

`make -C trust unseed-orthanc KIT=<CODE> PROJECTS="…"` (`seed_orthanc.py --remove-only`)
is the reverse: it deletes this trust's studies for the projects as the tables at
`HF_TRUST_DATA_REVISION` (or `TABLES_DIR=`) name them and uploads nothing — the PACS
half of moving off a re-cut project (see `trust/README.md`, "Unseeding").
