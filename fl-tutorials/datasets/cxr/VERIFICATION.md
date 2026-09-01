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

# CXR OMOP mock-data verification (FLIP#1092)

This records a run of the verification gate that backs the wave's provenance claim: that
`fl-tutorials/datasets/cxr/omop_convert_cxr.py` is what actually produced the `cxr_project` OMOP
tables published on `aicentreflip/trust-data`, not merely code that happens to resemble it.

## What is and is not being verified

The scope here is narrower than spleen's, and deliberately so. Only the **OMOP conversion** is
in-tree for cxr:

| stage | in this repo? | reproducible? |
| --- | --- | --- |
| synthetic chest X-ray generation | **No** &mdash; [`londonaicentre/xraycat`](https://github.com/londonaicentre/xraycat) (private) | out of scope |
| images &rarr; DICOM metadata table | No &mdash; same repo | out of scope |
| **metadata table &rarr; OMOP tables** (`omop_convert_cxr.py`) | **Yes** | **Yes** &mdash; a pure, deterministic transform |

This matches the acceptance criterion in FLIP#1092: *"the `cxr_project` OMOP conversion is in-tree
(image generation is out of scope &mdash; those images come from a synthetic model outside this
pipeline)"*. The provenance chain recorded here therefore starts at the DICOM metadata table, which
is published beside its outputs at
`omop-csv/<version>/cxr_project/source/dicom_metadata.csv` and is the actual input that produced the
published export &mdash; its 8332 rows yield exactly the 8332 `person_id`s in the published
`person.csv`, which is what makes it the canonical input rather than a plausible one.

## Procedure

```bash
make -C fl-tutorials fetch-cxr-metadata-table
make -C fl-tutorials build-cxr-omop-tables
make -C fl-tutorials verify-cxr-omop-tables
```

(equivalently `make -C fl-tutorials reproduce-cxr-omop`, which chains all three). The last step runs
`fl-tutorials/datasets/utils/verify_omop_tables.py --project cxr_project` &mdash; the same gate
script spleen uses, selected by `--project`. It fetches the published CSVs for the pinned data
version from `aicentreflip/trust-data` and diffs them against the locally generated tables (per-trust
generated output merged, sorted by primary key, compared column by column).

Needs no root, no image download, and no access to `xraycat`.

## Run record

- **Data version compared**: `20260729` (`trust/omop-db/.data_version`)
- **Date run**: 2026-09-01
- **Environment**: this worktree, `uv run --project datasets/cxr`

### Result (verbatim)

```
MATCH person: 8332 rows x 10 cols
MATCH procedure_occurrence: 8332 rows x 9 cols
MATCH visit_occurrence: 8332 rows x 8 cols
MATCH image_occurrence: 8332 rows x 11 cols
MATCH image_feature: 11660 rows x 8 cols
skip  measurement: not published for cxr_project
MATCH observation: 11660 rows x 7 cols

GATE PASS — every published table reproduces from 20260729
```

Exit code: `0`.

`measurement` is skipped, not failed: the gate's table list is the union across datasets, and
`measurement` is the one only `spleen_project` publishes. cxr's per-finding rows are `observation`
(with a paired `image_feature`) rather than measurements, because its features come from a synthetic
radiology report rather than from DICOM tags. A table absent upstream is absent by design.

The guard that stops a skip being a way to pass vacuously is the compared-count check. Verified
negatively:

```
$ uv run --project datasets/cxr python datasets/utils/verify_omop_tables.py \
    --project cxr_project --data-version 19990101
...
GATE FAIL — no tables were compared for cxr_project at version 19990101. ...
$ echo $?
1
```

### Per-table detail

| table | rows | shared cols | published-only cols | outcome |
| --- | --- | --- | --- | --- |
| `person` | 8332 | 10 | &mdash; | MATCH |
| `procedure_occurrence` | 8332 | 9 | &mdash; | MATCH |
| `visit_occurrence` | 8332 | 8 | &mdash; | MATCH |
| `image_occurrence` | 8332 | 11 | &mdash; | MATCH |
| `image_feature` | 11660 | 8 | &mdash; | MATCH |
| `observation` | 11660 | 7 | &mdash; | MATCH |

Note cxr needs **no** published-only column excusals, where spleen needed five. The two exports were
produced by different scripts against different schema subsets; nothing is being relaxed here.

## Outcome

**GATE PASS.** Every published `cxr_project` table for data version `20260729` reproduces
byte-for-byte on the shared columns from the in-tree converter, starting from the published metadata
table. This is the evidence that `fl-tutorials/datasets/cxr/omop_convert_cxr.py` is demonstrably what
produced FLIP's published cxr mock OMOP data.

## Re-running this check

Re-run `make -C fl-tutorials reproduce-cxr-omop` after any change to `omop_convert_cxr.py`, the
shared contract in `fl-tutorials/datasets/utils/`, or a bump of `trust/omop-db/.data_version`. A
`DIFF` or `GATE FAIL` means either a real regression in the converter or that the published data was
produced by code that has since diverged from what is in-tree &mdash; either way, investigate and
resolve before trusting the data, don't relax this file to match.
