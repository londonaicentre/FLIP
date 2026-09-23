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

# `flip.xnat` — data enrichment

A small XNAT REST client plus manifest-driven upload helpers for the FLIP **data enrichment**
stage: the platform step where a model developer adds whatever an app needs on top of the imaging
data FLIP already pulled into a Trust's XNAT — most commonly segmentation labels for a supervised
app (see `docs/source/user-guides/user-data-enrichment.rst`).

**Where this runs.** On the model developer's workstation inside the Trust network (or as an XNAT
Container Service job), authenticated as their own XNAT account — deliberately **not** part of the
FL training path. FL client containers hold no XNAT credentials and reach imaging data only through
the imaging-api proxy; this package is a separate, human-in-the-loop tool.

Built on `requests` rather than a full XNAT client library, so it stays import-light inside the FL
images that vendor `flip`. Endpoint shapes mirror what the Trust `imaging-api` already uses in
`imaging_api/services/upload.py` / `imaging_api/services/projects.py`.

## Public API (`flip.xnat`)

| Name | Kind | Purpose |
| ---- | ---- | ------- |
| `XnatClient` | class | Authenticated XNAT REST session. Construct via `from_env()` (`XNAT_HOST`/`XNAT_USER`/`XNAT_PASS`), `from_config_file(path)` (a `{"server", "user", "password"}` JSON file), or directly. |
| `XnatScan` | frozen dataclass | One scan, addressed by `accession_id`, `subject_id`, `experiment_id`, `scan_id`. |
| `EnrichmentItem` | frozen dataclass | One file to upload: `accession_id`, `file_path`, optional `target_filename`. |
| `EnrichmentSummary` | dataclass | Per-server outcome of one upload pass — counts of `uploaded` / `skipped_no_scan` / `skipped_no_resource` / `skipped_exists` / `failed`, plus `.ok`, `.resolved_any`, `.resolved`, and `.render()`. |
| `ServerOutcome` | dataclass | What happened at one XNAT server during `run_enrichment` — exactly one of `summary` or `error` is set, `fatal` says whether `error` should fail the whole run. |
| `EnrichmentReport` | dataclass | Aggregate outcome across every server visited — `.summaries`, `.requested`, `.resolved`, `.resolved_any`, `.scans_total`, `.fully_covered`, `.exit_code(...)`, `.render()`. |
| `read_manifest(path)` | function | Parse a manifest CSV (`accession_id`, `file_path`, optional `target_filename`) into `list[EnrichmentItem]`. |
| `upload_enrichment_files(client, project_id, items, ...)` | function | Upload every item into the one XNAT project behind `client`, returning an `EnrichmentSummary`. |
| `run_enrichment(clients, items, flip_project_id=..., ...)` | function | Upload the same manifest into **every** given XNAT server (one FLIP project id, resolved per server), returning an `EnrichmentReport`. |

**The naming contract.** FL apps pair an image with its label by filename — the spleen apps do
`str(image).replace("/input_", "/label_")` — so by default the target filename in XNAT is derived
from the image already sitting in the scan's `NIFTI` resource, swapping the `input_` prefix for
`label_` (`DEFAULT_RENAME`, overridable). That keeps the uploaded name in lock-step with whatever
DICOM-to-NIfTI conversion produced, instead of the caller guessing it. `upload_scan_resource_file`
never overwrites unless asked (`overwrite=True`): XNAT's `PUT` overwrites unconditionally, so the
client pre-checks with a `GET` to keep that guarantee.

**Multi-server rosters are self-selecting.** Each Trust's XNAT holds only its own studies, so
`run_enrichment` sends the whole manifest to every server in `clients` — an accession exists at
exactly one Trust, and the others report it as "no matching scan" — so nothing can be uploaded
twice and no per-server filtering is needed. A Trust that has not pulled the project at all is a
non-fatal skip when there is more than one server in the roster (an `XnatProjectNotFound` alone is
fatal); any other `XnatError` is always fatal.

## CLI: `flip-xnat`

Installed as the `flip-xnat` console script (`flip-utils/pyproject.toml`); also runnable as
`python -m flip.xnat`. One subcommand, `upload`:

```bash
flip-xnat upload --manifest manifest.csv --flip-project-id <uuid> [options]
```

| Option | Meaning |
| ------ | ------- |
| `--manifest PATH` (required) | CSV with `accession_id`, `file_path`, optional `target_filename` columns. |
| `--flip-project-id` / `--project-id` (mutually exclusive, one required) | Resolve the XNAT project by matching `secondary_ID` against the FLIP Central Hub project id, or address a known XNAT project id directly. |
| `--credentials-file PATH` (repeatable) | JSON `{"server", "user", "password"}` file; repeat once per Trust to enrich the whole roster in one run. Defaults to `XNAT_HOST`/`XNAT_USER`/`XNAT_PASS`. |
| `--resource NAME` | XNAT resource to write into (default `NIFTI`). |
| `--rename SOURCE:TARGET` | Prefix swap deriving the target filename from the image (default `input_:label_`). |
| `--overwrite` | Replace files already present. |
| `--dry-run` | Resolve and report, upload nothing. |
| `--allow-no-op` | Exit 0 even when no destination was resolved anywhere (default: that is an error, since the commonest way enrichment silently achieves nothing is every scan being skipped). |
| `--require-full-coverage` | Also fail unless every scan in every visited project now carries its enrichment file. |

Exit code and rendered summary come from `EnrichmentReport.exit_code()` / `.render()` — see their
docstrings in [`enrichment.py`](enrichment.py) for the exact rules (a roster member simply not
holding the project is never fatal on its own; every server failing, or any real failure, always is).

## Auth / environment variables

`XnatClient.from_env()` reads `XNAT_HOST`, `XNAT_USER`, `XNAT_PASS` — the same variables the XNAT
Container Service injects into jobs, so the same script runs unchanged on a workstation or inside a
container. All three are required; any missing raises `XnatError` naming which.

## Worked example: spleen label upload

The reference consumer is
[`fl-tutorials/datasets/spleen/upload_spleen_labels_to_xnat.py`](../../../fl-tutorials/datasets/spleen/upload_spleen_labels_to_xnat.py),
which builds an `EnrichmentItem` manifest from the MSD spleen download and the published
accession↔MSD-case mapping (`trust-data`'s `omop-csv/spleen_project/image_occurrence.csv`, pinned
by `trust/.data_version`), then calls `run_enrichment` directly rather than going through the CLI
(so it can add its own `--trust` filter and its own mapping-fetch/caching logic). Run it against
the checkout (`--with ./flip-utils`, from the repo root), not the PyPI release, so the enrichment
code matches this tree — `make -C fl-tutorials upload-spleen-labels` does the same via
`FLIP_UTILS_DIR`:

```bash
uv run --no-project --with ./flip-utils python fl-tutorials/datasets/spleen/upload_spleen_labels_to_xnat.py \
  --flip-project-id "$FLIP_PROJECT_ID" \
  --labels-dir fl-tutorials/data/spleen/images \
  --xnat-url http://127.0.0.1:8105 --xnat-url http://127.0.0.1:8107 \
  --xnat-user "$XNAT_USER" --xnat-password "$XNAT_PASS" \
  --dry-run
```

It is wired into the platform's end-to-end smoke test via `--data-enrichment-cwd` /
`--data-enrichment-cmd`, and into `make -C fl-tutorials upload-spleen-labels` — see the root
`AGENTS.md` ("Smoking the spleen segmentation app requires a data-enrichment step") for the full
runbook, including why enrichment must run after DICOM-to-NIfTI conversion and why every Trust in
the roster must be enriched, not just one.
