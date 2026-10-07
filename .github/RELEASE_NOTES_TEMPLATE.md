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

- **A trust can write its own access rules** (#1297) — an optional governance document, one TOML file per trust (`trust/governance.<CODE>.toml`, worked example `trust/governance.example.toml`), named by `ACCESS_POLICY_FILE` in the kit. `[disclosure]` can raise the trust's cohort floor above `COHORT_QUERY_THRESHOLD` (never lower it); `[access]` permits or denies an operation per project; `[fl_privacy.nvflare]` sets the NVFLARE client's site privacy filter. The document is operator-owned and mounted read-only: the Central Hub cannot set, read or override it. With no document, nothing changes.
- **Site upgrades say when a newer release exists** (#1332) — the upgrade verbs name a more recent platform release and its date. If the Central Hub already runs it, the operator can stop and move to it; if not, the verb only warns, because a site must not run ahead of the Central Hub.

## :warning: Breaking Changes

None. A trust without a governance document behaves as on v0.10.0, and nothing in this release changes the hub↔site contract.

## :arrows_counterclockwise: Site upgrade

<!-- The prompt to trust operators (FLIP#1204). Sites upgrade on their own schedule and to the
     release their hub runs; this section is how they learn what this release asks of them. -->

<!-- Fill the first three lines in for EVERY release; they are not boilerplate. "Ordering" is where a
     flag-day is announced: a payload-cipher change or an FL-framework bump means hub AND sites in one
     Deployment-Mode window, and a site left behind fails every task until it moves. -->

- **Required:** no, if you are on v0.7.0 or later. Upgrade to adopt a governance document (#1297). **Yes, and as a flag day, if you are on v0.6.x or earlier**: you cross v0.7.0's AES-256-GCM change on the way here, and that has no CBC fallback.
- **Ordering:** sites at their own pace, before or after the hub — the Central Hub is unchanged in this release. From v0.6.x, hub and sites move together in one Deployment-Mode window.
- **Refreshed kit needed:** no — the Hub-shared block is unchanged.
- **Adopting a governance document** (optional, after the upgrade): write `trust/governance.<CODE>.toml`, set `ACCESS_POLICY_FILE` in the kit, check it with `make -C trust check-governance KIT=<CODE>`, and apply it with `make -C trust reload-governance KIT=<CODE>` — never `up-trust` / `restart-trust`, which are first-install verbs. `[fl_privacy.nvflare]` and `FL_SITE_PRIVACY_*` together are refused: use one. On Kubernetes, `sync-kit` validates the document and embeds it in the release (`trust/deploy/helm/README.md`). An EC2 trust driven over a remote Docker endpoint refuses a document in this release.
- **Operator command**, on the trust host, from your FLIP checkout:
  ```bash
  git fetch --tags origin && git checkout {{TAG}}        # the compose files and the verb come from the checkout, not the images
  sudo -E make upgrade-onprem-trust KIT=<slot>           # defaults to the release the hub runs; TAG={{TAG}} pins it before the hub moves
  ```
  Kubernetes: `make -C trust/deploy/helm upgrade-trust-k8s KIT=<CODE> PROD=<env> TAG={{TAG}}`; EC2: `make -C deploy/providers/AWS upgrade-trust-ec2 KIT=<CODE> PROD=<env> TAG={{TAG}}` — both from a checkout at {{TAG}}. Runbook: *docs → System administrators → Upgrading a site*. Sites on v0.7.0 or earlier do not have the command until they check out the tag.

## :seedling: New Features

- The trust governance document (#1297): `[disclosure] min_cohort_size`, `[access]` permit/deny rules over project (UUID) and operation — any matching deny denies, otherwise the strictest matching permit applies, and an action the document names but no rule matches is denied — and `[fl_privacy.nvflare]` for the NVFLARE site privacy filter. A denial is answered like a below-threshold cohort, so it reveals nothing about the policy; the rule id goes to the trust's own log. Invalid documents stop the service at startup. `[fl_privacy.flower]` is refused, since nothing enforces it on Flower yet.
- `make -C trust check-governance` validates a document with the same loader the service uses, and `make -C trust reload-governance` applies an edited one to a live trust without touching its data; data-access-api logs one `[governance] … sha256=…` line at startup, which the reload checks (#1297).
- The site-upgrade verbs name a newer platform release and its date, and offer it when the Central Hub already runs it. GitHub is advisory: a host that cannot reach it prints one line and carries on (#1332).

## :bug: Bug Fixes

<!-- Update this section if a fix lands before the cut. -->

- The Helm chart ships only the chart. It had no `.helmignore`, so each release record carried the chart's tests, scripts and any local caches, and a stray cache could push the record past the 1 MiB Secret limit (`Too long`) and fail the upgrade (#1339).

## :white_check_mark: Release checks

<!-- The gates that cannot run in CI: no GitHub-hosted runner has a GPU, and both the suite and the smoke test need real hardware and a full stack. Tick these on the release branch before the tag is cut — this section is the record that the published examples and the platform path were run, and it is shared by both release trains. See *Pre-release checklist* in CONTRIBUTING.md. -->

Tutorial suite, on a GPU host:

- [ ] NVFLARE — `make -C fl-tutorials run-all-tutorials`
- [ ] Flower — `make -C fl-tutorials run-all-tutorials FL_BACKEND=flower`
- [ ] Host and date recorded: <!-- e.g. "RTX 5090 workstation, 24 September 2026" -->

Not run for v0.11.0: the release was cut the same day as its last changes merged, and the suite takes several hours per backend.

Full-platform smoke test, against a running deployment:

- [x] NVFLARE — `make e2e_smoke`
- [x] Flower — `make e2e_smoke FL_BACKEND=flower`

Both passed on 29 September 2026 on the dev stack on an RTX 5090 workstation, against one trust (`--trusts GSTT`), with the hub, the trust services and the FL images (`sha-968a60d`) all at the release commit: create project, cohort query, trust approval, image pull, training, results uploaded and downloaded. Flower reused the NVFLARE run's project.

## :file_folder: PRs merged in this release

<!-- auto-populated by the release workflow -->

## :star: Acknowledgements

A big thank you to the following contributors for their work on this release:

<!-- auto-populated by the release workflow -->

---
