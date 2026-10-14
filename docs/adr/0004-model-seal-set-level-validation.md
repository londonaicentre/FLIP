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

# Bundle governance runs at an explicit seal step, not per-file and not at bundling time

Every validation FLIP has today is **per-file at scan time**: `reconcile_uploaded_file()` sees one
object, picklescan and Bandit run on it, `_finalise_scan_status()` promotes it into `scanned/`.
Every validation the MONAI bundle on-ramp needs is **per-set**: "no `.py` anywhere in this
bundle" needs the complete file list, the config analyser must follow `@refs` and `%macros` across
`configs/`, and cohort/XNAT validation needs `monai-flip.yaml` together with the `.sql` it names.
There was no event in FLIP where a complete upload set is known and validated, so we add one:
**`POST /models/{id}/seal`**, which runs every set-level gate and sets `flip_ready`. Approval is
gated on `flip_ready`.

`job_type` also moves onto the `Model` row, declared at creation. Today it is discovered by
downloading `config.json` from S3 inside `bundle_nvflare_application()`, i.e. at training start,
long after a human approved the model. Declaring it up front lets `presigned_url_for_upload` reject
a `.py` for a `monai_bundle` model at policy-mint time, before the object ever reaches S3. The
column is **nullable**: when unset, the existing `config.json` inference applies unchanged, so
every current job type keeps working and `monai_bundle` is simply the first type that requires it
declared.

## Considered options

- **Enforce at bundling time only** (no new endpoint, no migration). Rejected: it inverts the
  governance claim. The researcher uploads, the per-file scans pass because `.py` is a permitted
  extension, an admin approves, training starts, and only then does the bundle get rejected. The
  human approved an artefact that nothing had validated.
- **Run the analyser per-file inside the existing scan pipeline.** Rejected as insufficient rather
  than wrong: it fits `malware_scan_service.py` neatly, but it cannot see the set, so the no-`.py`
  rule and the overlay/`.sql` cross-check have nowhere to live and fall back to bundling time
  anyway.

## Consequences

- One Alembic migration covers both new `Model` columns (`job_type`, `flip_ready`), satisfying the
  project's same-PR migration rule and the `test_migrations.py` drift guard.
- **The plan's claim that no flip-ui work is needed is false.** The UI gains a job-type selector at
  model creation, a seal action, `flip_ready` state, and validation-error surfacing. The selector
  extends existing machinery: `flip-ui/src/services/model-service.ts` already fetches
  `/model/job-types` and derives per-type required files.
- Seal is idempotent and re-runnable: uploading another file clears `flip_ready`, so a set cannot be
  sealed, extended, and approved on the strength of the earlier seal.
