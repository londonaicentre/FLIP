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

- **Sites can be upgraded to a named release** (#1204, via #1205 and #1285) — every image is built at the `v*` tag, and each deployment path gains a data-safe upgrade verb: `upgrade-onprem-trust`, `upgrade-trust-k8s`, `upgrade-trust-ec2` and `upgrade-xnat`. A registry preflight refuses a tag whose images are not all published, the XNAT database is dumped before the stack moves, and a `v*` target is refused unless the checkout is at that tag. **This is the first release that carries the verb** — a site on v0.7.0 or earlier gets it by checking out the tag first.
- **Trust data has one path: seeding** (#1190) — `make up` starts each trust's omop-db and Orthanc on empty volumes and seeds them from the published canonical tables at the version pinned in `trust/.data_version`. The pgdata and Orthanc volume snapshots are retired.
- **The cohort disclosure floor counts patients, not rows** (#1197) — `COHORT_QUERY_THRESHOLD` now resolves distinct subjects via `person_id`, or through `omop.image_occurrence` when only `accession_id` is present. Ten X-rays from one patient no longer read as ten subjects. A cohort exposing neither column is refused. Cohort modality distribution ships alongside it.
- **The docs GIFs leave git** (#1236, via #1238) — each recording is published to the `aicentreflip/docs-gifs` Hugging Face dataset under an immutable tag and fetched at docs-build time, with `docs/.gifs_version` as the one pin; the repository stops carrying binary churn.
- **PyTorch 2.13** across the FL images and tutorials (#439, #475, via #1268), which also restores tutorial simulation on macOS.
- **A brain MRI tutorial dataset** from MSD Task01_BrainTumour (#1221, via #1224) — tables-only publish with deterministic DICOM generated locally, and plastimatch out of the spleen chain.
- **`AGENTS.md` is the single agent-instruction file** (#1251) — all nine `CLAUDE.md` copies are gone, at every level, as file or symlink.
- **flip-utils is type- and format-gated like every other service** (#1247, via #1248) — mypy and `ruff format --check` in both `make -C flip-utils unit-test` and CI, with the 58 accumulated errors cleared.

## :warning: Breaking Changes

- **A cohort that passed the disclosure threshold before may now be refused** (#1197). The floor counts distinct subjects rather than rows, so a cohort of ten studies belonging to three patients now falls below a threshold of 10. Both row-level routes are gated — `/cohort/dataframe` and `/cohort/accession-ids` — and the cohort is re-evaluated live on every call, so an imported project can begin refusing later. A cohort resolving neither `person_id` nor `accession_id` is refused outright.
- **Trust volumes are seeded, not restored** (#1190). The pgdata and Orthanc volume-snapshot path is gone; `make up` seeds from the canonical tables at `trust/.data_version`. A trust carrying data from the old path keeps it — seeding is marker-guarded — but the snapshot tooling it came from no longer exists.
- **The docs GIFs are no longer in the repository** (#1238). A docs build fetches them from the Hugging Face dataset at the tag in `docs/.gifs_version`; a build with no network reaches the fetcher, not a local file. Anything referencing the old in-tree GIF paths needs updating.
- **`CLAUDE.md` is gone and must not be reintroduced** (#1251). Claude Code reads `AGENTS.md` only from v2.1.277 onward, only where no CLAUDE-named file exists in the project, and not at all on Bedrock, Vertex or Foundry — so a personal `CLAUDE.md` anywhere at or above the checkout silently suppresses every `AGENTS.md` in the tree. Personal instructions belong in `~/.claude/`.
- **PyTorch moves to 2.13** (#1268). The FL images rebuild on it; uploaded app code pinned to an older torch, or relying on a removed API, needs re-testing before it runs on this release.
- **EC2 hosts no longer replace themselves on AMI drift** (#1282). The Ubuntu AMI data source is now ignored for changes, so a new upstream AMI stops silently destroying and recreating the bastion and trust hosts on the next apply — and equally, those hosts no longer pick up a new base image by themselves. Replacing one is now a deliberate act.

## :arrows_counterclockwise: Site upgrade

<!-- The prompt to trust operators (FLIP#1204). Sites upgrade on their own schedule and to the
     release their hub runs; this section is how they learn what this release asks of them. -->

<!-- Fill the first three lines in for EVERY release; they are not boilerplate. "Ordering" is where a
     flag-day is announced: a payload-cipher change or an FL-framework bump means hub AND sites in one
     Deployment-Mode window, and a site left behind fails every task until it moves. -->

- **Required:** no, if you are already on v0.7.0 — nothing in this release changes the hub↔site payload contract, so upgrade at your convenience. **Yes, and as a flag day, if you are on v0.6.x or earlier**: you cross v0.7.0's AES-256-GCM change on the way here, and that has no CBC fallback.
- **Ordering:** hub first, sites at their own pace — *unless* you are coming from v0.6.x, in which case hub and sites move together in one Deployment-Mode window and a site left behind answers every task `Invalid payload: failed authentication`.
- **Refreshed kit needed:** no — the Hub-shared block is unchanged. (If the hub's AES key or FL kit date changed with your hub deploy, re-sync: `make sync-trust-kit KIT=<CODE> PROD=<env>` → `make -C deploy/providers/AWS package-onprem-trust-kit KIT=<CODE>`.)
- **Operator command**, on the trust host, from your FLIP checkout:
  ```bash
  git fetch --tags origin && git checkout {{TAG}}        # the compose files and the verb come from the checkout, not the images
  sudo -E make upgrade-onprem-trust KIT=<slot>           # defaults to the release the hub runs; TAG={{TAG}} pins it before the hub moves
  ```
  Kubernetes: `make -C trust/deploy/helm upgrade-trust-k8s KIT=<CODE> PROD=<env> TAG={{TAG}}`; EC2: `make -C deploy/providers/AWS upgrade-trust-ec2 KIT=<CODE> PROD=<env> TAG={{TAG}}` — both from a checkout at {{TAG}}. Runbook: *docs → System administrators → Upgrading a site*. Sites on v0.7.0 or earlier do not have the command until they check out the tag; this is the first release whose images are published at a `v*` tag.

## :seedling: New Features

- Repeatable site upgrades: `v*`-tagged images, the per-path upgrade verbs, registry preflight, the XNAT pre-upgrade dump and the upgrade runbook (#1205, #1285).
- Trust seeding from the canonical dataset at bring-up, versioned by `trust/.data_version` (#1190).
- Per-subject cohort disclosure accounting and cohort modality distribution (#1197).
- The brain MRI tutorial dataset with a deterministic NIfTI→DICOM writer and tables-only publish (#1224); a sim-only `MAX_SAMPLES` cap for the three Ark+ tutorials (#1279).
- Docs GIFs published to a Hugging Face dataset and pinned by `docs/.gifs_version` (#1238, #1275).
- Local UI ports pre-registered as browser origins on the dev estate, so a second checkout's UI can sign in (#1227, via #1229).

## :bug: Bug Fixes

- **AWS / infrastructure**: a new upstream Ubuntu AMI silently replacing the bastion and trust EC2 hosts on the next apply (#1281, via #1282); a held Terraform apply and the not-yet-on-main drift dispatch giving no legible reason (#1161, via #1162).
- **FL / tutorials**: PyTorch 2.13 restoring tutorial simulation on macOS (#1268).
- **Dependencies**: anyio floored at >=4.14.2 across the seven uv projects that lock it, clearing 15 Dependabot alerts (#1264); soupsieve floored at >=2.9.0 in flip-utils, clearing two ReDoS alerts (#1267).
- **CI**: flip-utils' unenforced mypy config, which had accumulated 58 errors, and an ungated `ruff format` (#1248).

## :file_folder: PRs merged in this release

<!-- auto-populated by the release workflow -->

## :star: Acknowledgements

A big thank you to the following contributors for their work on this release:

<!-- auto-populated by the release workflow -->

---
