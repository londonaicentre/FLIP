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

# Spleen OMOP mock-data verification (FLIP#1092)

This records a run of the verification gate that backs the wave's provenance claim: that the
vendored conversion chain under `fl-tutorials/datasets/spleen/` is what actually produced the
`spleen_project` OMOP tables published on `aicentreflip/trust-data`, not merely code that
happens to resemble it.

## What is and is not being verified

As set out in the design spec
(`docs/superpowers/specs/2026-09-01-omop-mock-data-provenance-design.md`, "What is and is not
reproducible"), only one stage of the chain is exactly reproducible:

| stage | reproducible? |
| --- | --- |
| MSD NIfTI &rarr; DICOM (`convert_spleen_dataset.py`) | **No** &mdash; synthesises a fresh patient population (names, sex, dates, `AccessionNumber`, and `StudyInstanceUID` via `pydicom.uid.generate_uid()`) on every run |
| DICOM &rarr; metadata table (`create_metadata_table.py`) | No &mdash; inherits those synthesised identities |
| **metadata table &rarr; OMOP tables** (`omop_convert_spleen.py`) | **Yes** &mdash; a pure, deterministic transform |

This run therefore starts from the **published metadata table** &mdash; the actual input that
produced the published export, fetched from
`omop-csv/spleen_project/source/dicom_metadata.csv` at the pinned data-version tag &mdash; rather than regenerating
DICOMs from the raw MSD download. Regenerating would synthesise a new, non-matching patient
population, so it could never reproduce the published tables and is not what this gate checks.
The DICOM-generation stage is verified only structurally (the vendored script runs, produces
DICOM output shaped as the pipeline expects, and its own published output is what this gate
diffs against) &mdash; it cannot be bit-reproduced, by design of the deidentification step it
performs.

## Procedure

```bash
make -C fl-tutorials fetch-spleen-metadata-table
make -C fl-tutorials build-spleen-omop-tables
make -C fl-tutorials verify-spleen-omop-tables
```

(equivalently `make -C fl-tutorials reproduce-spleen-omop`, which chains all three). The last
step runs `fl-tutorials/datasets/utils/verify_omop_tables.py --project spleen_project` &mdash; one
gate script shared by every dataset, since nothing in it is spleen-specific. It fetches the
published CSVs for the pinned data version from `aicentreflip/trust-data` and diffs them against
the locally generated tables (per-trust generated output merged, sorted by primary key, compared column by
column). Two categories of published-only column are tolerated as known-benign: optional schema
columns the converter legitimately omits (`wadors_uri` on `image_occurrence`; `alg_datetime`,
`alg_system`, `image_finding_concept_id`, `image_finding_id` on `image_feature`), verified empty
in the published export before being excused &mdash; a published-only column carrying real data
would still fail the gate.

## Run record

- **Data version compared**: `20260729` (`trust/omop-db/.data_version`)
- **Date run**: 2026-09-01
- **Environment**: this worktree, no root, no MSD/DICOM download &mdash; the reproducible path only

### Result (verbatim)

```
MATCH person: 41 rows x 10 cols
MATCH procedure_occurrence: 41 rows x 9 cols
MATCH visit_occurrence: 41 rows x 8 cols
MATCH image_occurrence: 41 rows x 11 cols  (+1 empty published-only col(s))
MATCH image_feature: 205 rows x 8 cols  (+4 empty published-only col(s))
MATCH measurement: 205 rows x 11 cols
skip  observation: not published for spleen_project

GATE PASS — every published table reproduces from 20260729
```

Exit code: `0`.

`observation` is skipped, not failed: the gate's table list is the union across datasets, and
`observation` is the one only `cxr_project` publishes (spleen publishes `measurement`, which cxr
does not). A table absent upstream is absent by design. The guard that stops this being a way to
pass vacuously is the compared-count check &mdash; a run that skips *everything* is a `GATE FAIL`,
verified by pointing the gate at a nonexistent data version.

### Per-table detail

| table | rows | shared cols | published-only cols (empty, excused) | outcome |
| --- | --- | --- | --- | --- |
| `person` | 41 | 10 | &mdash; | MATCH |
| `procedure_occurrence` | 41 | 9 | &mdash; | MATCH |
| `visit_occurrence` | 41 | 8 | &mdash; | MATCH |
| `image_occurrence` | 41 | 11 | 1 (`wadors_uri`) | MATCH |
| `image_feature` | 205 | 8 | 4 (`alg_datetime`, `alg_system`, `image_finding_concept_id`, `image_finding_id`) | MATCH |
| `measurement` | 205 | 11 | &mdash; | MATCH |

## Outcome

**GATE PASS.** Every published `spleen_project` table for data version `20260729` reproduces
byte-for-byte on the shared columns from the vendored, in-tree conversion chain, starting from
the published metadata table. This is the evidence that the vendored code under
`fl-tutorials/datasets/spleen/` (Tasks 1&ndash;7 of this wave) is demonstrably what produced
FLIP's published spleen mock OMOP data, not merely a plausible reimplementation.

## Re-running this check

Re-run `make -C fl-tutorials reproduce-spleen-omop` after any change to
`omop_convert_spleen.py`, the shared schemas in `fl-tutorials/datasets/utils/`, or a bump of
`trust/omop-db/.data_version`. A future run against a newer `.data_version` is expected to keep
passing as long as the published export for that version was itself produced by this same
chain; a `DIFF` or `GATE FAIL` at that point means either a real regression in the converter or
that the published data was produced by code that has since diverged from what's vendored here
&mdash; either way, investigate and resolve before trusting the data, don't relax this file to
match.
