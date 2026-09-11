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

# :package: {{PROJECT}} {{VERSION}} Release Notes

## :sparkles: Highlights

- **FL frameworks move to current GA releases** — NVFLARE 2.8.0 → 2.9.0 (#1174, via the 2.8.1 interim in #1046) and Flower → 1.36.0 (#1175).
- **Real Trust PACS support** (#994) — XNAT's DICOM receiver and web UI get separate host ports (`XNAT_PORT` / `XNAT_WEB_PORT`), the upstream PACS is configured per trust (`PACS_HOST` / `PACS_AETITLE` / `PACS_QR_PORT`, retrieval throttle, extended-negotiation flag), and imaging-api reads the PACS id from XNAT instead of assuming 1.
- **Trust data: one copy, versions as tags** (#1101) — `aicentreflip/trust-data` holds every artefact once at an unversioned path; a data version is a git tag pinned by the single `trust/.data_version`. The new seed pipeline (`make -C trust seed-trusts PROJECTS=…`) loads any published project into a running trust by `source_trust`. OMOP mock-data generation is reproducible in-tree and gated against the published export (#1097), with the dataset tooling consolidated under `fl-tutorials/datasets/` (#1070).
- **Terraform runs in CI** (#1082) — OIDC-authenticated plan on every PR touching `deploy/providers/AWS/**`, apply on merge (`develop` → stag, `main` → prod), nightly drift detection, plus a checkov security lint (#1057) and LZA-aligned CloudWatch retention (#1156).
- **Per-trust FL privacy policy** (#853) — each trust enforces its own NVFLARE site-side `privacy.json`.
- **Tabular-only projects** (#1129) — a project can opt out of the imaging stage (`has_imaging`), so EHR-only cohorts skip XNAT entirely.
- **UI** — one Models page with a project filter and unified tables (#1014); consistent per-trust colours and sorted metrics plots (#1012); dependency scoping enforced in CI and Mirage no longer shipped (#1061).
- **Dev experience** — a second dev hub on one host via `FLIP_INSTANCE` (#958); 13 local-setup blockers fixed across macOS, non-interactive runs and the K8s trust chart (#1010); the published FL images run with any host UID (#1172); SES is out of dev deployments (#1083).

## :warning: Breaking Changes

- **Merging to `main` now applies production infrastructure** (#1082). The previous "don't `make apply` for prod" rule is superseded; the FL quiesce gate holds any apply that would replace `fl-server-net-1` / `fl-api-net-1` until re-dispatched with `fl_quiesced: true`.
- **Trust-data layout** (#1101): the per-service `trust/omop-db/.data_version` and `trust/orthanc/.data_version` pins are replaced by one `trust/.data_version`, and the versioned filenames (`trust<N>_pgdata_<v>.tar`, `omop-csv/<v>/`) are no longer read — every consumer fetches `resolve/<tag>/<unversioned path>`. Trust hosts must run this release's scripts before the legacy files are removed from the dataset.
- **XNAT ports** (#994): `XNAT_PORT` is now the DICOM SCP receiver port only; the host-published web UI port is the new `XNAT_WEB_PORT`, and the two must differ. Trust kit files need both.
- **Flower apps require `config.json`** (#991, #1056): the hub no longer guesses a job type; a missing `config.json` fails loudly instead of defaulting to `standard`. Flower also needs `.toml` in `ALLOWED_MODEL_FILE_EXTENSIONS`.
- **FL client image scope** (#954): each fl-client mounts only its own net's slice of the images tree; training code paths are unchanged, but host directories must be pre-created writable per net.
- **NVFLARE 2.9.0** (#1174) carries upstream breaking changes for custom jobs; see the tutorial and template updates in that PR.
- **Database migration** (#1129): flip-api adds the `has_imaging` column via Alembic; it runs at boot.

## :seedling: New Features

- Real Trust PACS configuration and DQR (#994); tabular-only projects (#1129); per-trust FL privacy policy (#853).
- Seed pipeline and tag-versioned trust data (#1101); reproducible OMOP mock-data provenance (#1097); shared `fl-tutorials/datasets/` tree (#1070).
- One Models page (#1014); torch.jit-free MAP bundle form (#1020); second dev hub (#958); SES removed from dev (#1083); CloudWatch retention aligned with the LZA baseline (#1156).
- Terraform plan/apply/drift in CI (#1082) with checkov lint (#1057).

## :bug: Bug Fixes

- **Security**: any authenticated user could read any user's full profile (#943); `callback_urls` is the live CORS allowlist and prod included localhost (#1088); cloud trust EC2 kit fetch — KMS grant, silent no-op and cross-trust key exposure (#1009); the app bundle is now an allowlist, shipping only the app folders plus the backend's root file (#1008).
- **Trust / imaging**: cohort downloads cached server-side and each FL client scoped to its own net (#954); XNAT dcm2niix pinned to a stale build that silently dropped slices (#981); imaging import status going stale after a failed refresh (#1023); dcm2niix command mismatch mis-reported as a missing plugin (#1094); XNAT bind mounts provisioned as the container uid (#1096); fl-client kit volume ignoring `flBackend` (#1000); soft-deleting a project no longer tries to delete trust imaging (#964).
- **FL / images**: published NVFLARE images crash-looped unless the host UID was 1000 (#1172); `FLIP_Session` narrowing its NVFLARE base signature (#1034); single-trust hang and job-type guessing (#991); spleen downloader picking cases lexicographically (#1062); spleen enrichment no longer depends on a private repo (#955); `.data_version` path after the datasets move (#1103).
- **Hub / UI**: first login after MFA enrolment failing for up to 60 s (#1016); metrics plot colours (#1012); flip-ui dependency scoping (#1061) and the typecheck regression (#1076); trust-api `.dockerignore` (#1004); local dev setup blockers (#1010); Ark+ demo re-capture (#1029).

## :file_folder: PRs merged in this release

<!-- auto-populated by the release workflow -->

## :star: Acknowledgements

A big thank you to the following contributors for their work on this release:

<!-- auto-populated by the release workflow -->

---
