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

# Prostate OMOP mock-data verification (FLIP#1100 follow-on)

This records the run of the verification gate that backs the provenance claim for
`prostate_project`: that `fl-tutorials/datasets/prostate/omop_convert_prostate.py`, fed the two
`source/` tables published beside its output, is what produced the `prostate_project` OMOP tables on
`aicentreflip/trust-data` at data-version tag `20260902` — and that the published DICOM set is exactly
the imaging those tables describe.

## What is and is not being verified

Unlike cxr, every stage is in-tree, because the chain starts at a public download:

| stage | in this repo? | reproducible? |
| --- | --- | --- |
| PI-CAI fold download (`download_data.py`) | **Yes** | Yes &mdash; Zenodo record 6624726, fold 0 |
| `.mha` &rarr; DICOM (`convert_mha_to_dicom.py`) | **Yes** | Yes &mdash; deterministic UIDs (SHA-512 of the identifiers), no clock, no salted hash |
| DICOM &rarr; metadata table + trimmed marksheet (`create_prostate_metadata_table.py`) | **Yes** | Yes |
| **metadata table + marksheet &rarr; OMOP tables** (`omop_convert_prostate.py`) | **Yes** | **Yes** &mdash; a pure, deterministic transform |
| OMOP tables &harr; DICOM set (`trust/orthanc/publish_dicom.py`) | **Yes** | Yes &mdash; both-ways membership check |

The gate below exercises the last two rows from the **published inputs** (the reproducible path:
no 5 GB download, no conversion). The full regeneration path was also run for this record, and
because every UID is a digest of stable identifiers it reproduces the published tables byte for
byte too &mdash; the property the spleen chain does not have.

## Procedure

```bash
make -C fl-tutorials fetch-prostate-metadata-table    TRUST_DATA_REVISION=20260902
make -C fl-tutorials build-prostate-omop-tables
make -C fl-tutorials verify-prostate-omop-tables      TRUST_DATA_REVISION=20260902
```

(equivalently `make -C fl-tutorials reproduce-prostate-omop TRUST_DATA_REVISION=20260902`, which
chains all three). The last step runs `fl-tutorials/datasets/utils/verify_omop_tables.py --project
prostate_project --trusts trust_1 trust_2 trust_3` &mdash; the same gate script spleen and cxr use;
three per-trust splits because `source_trust` is one contributing center per trust (ZGT 1, PCNN 2,
RUMC 3). It fetches the published CSVs at the given revision and diffs them against the locally
generated tables (per-trust output concatenated, sorted by primary key, compared column by column).
`TRUST_DATA_REVISION` names the tag explicitly here because `trust/.data_version` did not yet
point at a tag carrying `prostate_project` when this was run.

## Run record

- **Data version compared**: tag `20260902` on `aicentreflip/trust-data`
- **Date run**: 2026-09-02
- **Environment**: this worktree, `uv run --project datasets/prostate`

### Result (verbatim)

```
✅ prostate source tables at fl-tutorials/data/prostate/source (revision 20260902)
trust_1: 76 persons, 228 series
trust_2: 68 persons, 207 series
trust_3: 151 persons, 465 series
MATCH person: 295 rows x 7 cols
MATCH procedure_occurrence: 300 rows x 9 cols
MATCH visit_occurrence: 300 rows x 8 cols
MATCH image_occurrence: 900 rows x 11 cols
MATCH image_feature: 4500 rows x 8 cols
MATCH measurement: 5296 rows x 11 cols
MATCH observation: 900 rows x 10 cols

GATE PASS — every published table reproduces from 20260902
```

Exit code: `0`. Nothing is skipped: prostate publishes every table the gate knows, including both
`measurement` (DICOM attributes plus PSA / PSA density / prostate volume) and `observation` (ISUP
grade group, csPCa, PI-RADS). No published-only column excusals were needed.

### Per-table detail

| table | rows | shared cols | outcome |
| --- | --- | --- | --- |
| `person` | 295 | 7 | MATCH |
| `procedure_occurrence` | 300 | 9 | MATCH |
| `visit_occurrence` | 300 | 8 | MATCH |
| `image_occurrence` | 900 | 11 | MATCH |
| `image_feature` | 4500 | 8 | MATCH |
| `measurement` | 5296 | 11 | MATCH |
| `observation` | 900 | 10 | MATCH |

### The DICOM set

`trust/orthanc/publish_dicom.py --project prostate_project --tables-dir <canonical> --source
<dicom tree>` on the same day, before publishing:

```
source: 19768 instances, 300 accessions; published: 300 studies
per source_trust: {'1': 76, '2': 69, '3': 155}

VERIFY PASS — .../data/prostate/dicom is exactly prostate_project in .../data/prostate/canonical
```

Every accession in the tables has a DICOM directory and vice versa, every StudyInstanceUID and
PatientID is in the tables, no accession spans two studies, no SOPInstanceUID repeats. The archive
published as `dicom/prostate_project.tar.gz` (2.58 GB) was cut from that verified tree.

## Outcome

**GATE PASS.** Every published `prostate_project` table at tag `20260902` reproduces byte for byte
from the in-tree converter, starting from the published `source/` tables, and the published DICOM
set is exactly the imaging those tables describe. This is the evidence that
`fl-tutorials/datasets/prostate/` is demonstrably what produced FLIP's prostate mock data.

## Re-running this check

Re-run `make -C fl-tutorials reproduce-prostate-omop` after any change to the converter or the
metadata-table builder, to the shared contract in `fl-tutorials/datasets/utils/`, or a bump of
`trust/.data_version` (pass `TRUST_DATA_REVISION=<tag>` while the pin names an older tag). A
`DIFF` or `GATE FAIL` means a real regression or published data that no longer matches the code
&mdash; investigate and resolve, don't relax this file to match.
