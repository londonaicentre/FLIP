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

- **Approving a project becomes the participating site's decision rather than the hub's** (#1298) — approving a project for a named trust now requires `CAN_APPROVE_FOR_TRUST` *at that trust*. The platform-wide administrator role does not carry it, so a hub administrator can no longer approve on every site's behalf, and each trust named in the call has to authorise it or the approval is refused in full. Who approved is recorded with the approval.
- **A trust can state its runtime access policy as a document** (#1297) — an optional governance file (`ACCESS_POLICY_FILE`, or the document inline as `ACCESS_POLICY`) states the cohort threshold, permit/deny rules over each cohort operation, and the site-privacy policy as data instead of settings compiled into the services. It can only tighten a trust's posture, an operation no rule mentions keeps its current behaviour, and the hub never writes it. Validate before applying: `make -C trust check-governance KIT=<CODE>`.
- **Trust-scoped roles** (#1266) — the `TRUST_OWNER` role and the authorisation behind it: `user_role` gains a trust dimension, so a permission can be held *at a trust* and not only platform-wide. Existing deployments grant the role to the administrators of each trust, and the Trust control panel shows who owns one.
- **XNAT trusts can point at a real Trust PACS** (#1234) — the DICOM SCP gets a Service of its own, separate from the web UI, with a fixed node port or a load balancer for a PACS outside the cluster, and the chart refuses to render a real-PACS install that would expose the console in order to reach it.
- **The published tutorials and the platform path become release gates** (#1306) — neither can run in CI (no hosted runner has a GPU, and the smoke test needs a full stack), so the GPU tutorial suite on both backends and `make e2e_smoke` are ticked in the *Release checks* section of this file and in the pre-release checklist.

## :warning: Breaking Changes

- **Approval no longer works from a platform-wide grant alone** (#1298). A single `CAN_APPROVE_PROJECTS` on the seeded Admin role used to approve a project for every trust; from this release the call requires `CAN_APPROVE_FOR_TRUST` at *each* trust it names, and a platform-wide grant does not satisfy it. Existing deployments are migrated: the administrators of each trust are granted ownership of it, so the people who approved before still can — for their own trust. A trust with no owner cannot be approved for until one is named.
- **DICOM exposure moves to its own Service on Helm trusts** (#1234). `xnat-web.service` used to be what exposed DICOM; it is now held at `ClusterIP`, so a DICOM exposure can never publish the Tomcat console with it. An install that set it to `NodePort` to reach a real PACS must move to `dicomService.type` (with `dicomNodePort`) or `LoadBalancer`, and reach the console with `kubectl port-forward`. The chart fails that render (`validatePacsReachable`) rather than reverting the exposure quietly.

## :arrows_counterclockwise: Site upgrade

<!-- The prompt to trust operators (FLIP#1204). Sites upgrade on their own schedule and to the
     release their hub runs; this section is how they learn what this release asks of them. -->

<!-- Fill the first three lines in for EVERY release; they are not boilerplate. "Ordering" is where a
     flag-day is announced: a payload-cipher change or an FL-framework bump means hub AND sites in one
     Deployment-Mode window, and a site left behind fails every task until it moves. -->

- **Required:** no, if you are on v0.7.0 or later — nothing in this release changes the hub↔site payload contract, and the governance document is optional: with none configured the platform defaults apply exactly as before. **Recommended for Kubernetes trusts**, which can now reach a real PACS (#1234). **Yes, and as a flag day, if you are on v0.6.x or earlier**: you cross v0.7.0's AES-256-GCM change on the way here, and that has no CBC fallback.
- **Ordering:** hub first, sites at their own pace — *unless* you are coming from v0.6.x, in which case hub and sites move together in one Deployment-Mode window and a site left behind answers every task `Invalid payload: failed authentication`.
- **Refreshed kit needed:** no — the Hub-shared block is unchanged. A governance document, if you adopt one, is yours to own: copy `trust/governance.example.toml`, point `ACCESS_POLICY_FILE` at it from your own kit file, and validate it with `make -C trust check-governance KIT=<CODE>` before `make -C trust up-trust KIT=<CODE>`. (If the hub's AES key or FL kit date changed with your hub deploy, re-sync: `make sync-trust-kit KIT=<CODE> PROD=<env>` → `make -C deploy/providers/AWS package-onprem-trust-kit KIT=<CODE>`.)
- **Operator command**, on the trust host, from your FLIP checkout:
  ```bash
  git fetch --tags origin && git checkout {{TAG}}        # the compose files and the verb come from the checkout, not the images
  sudo -E make upgrade-onprem-trust KIT=<slot>           # defaults to the release the hub runs; TAG={{TAG}} pins it before the hub moves
  ```
  Kubernetes: `make -C trust/deploy/helm upgrade-trust-k8s KIT=<CODE> PROD=<env> TAG={{TAG}}`; EC2: `make -C deploy/providers/AWS upgrade-trust-ec2 KIT=<CODE> PROD=<env> TAG={{TAG}}` — both from a checkout at {{TAG}}. Runbook: *docs → System administrators → Upgrading a site*. Sites on v0.7.0 or earlier do not have the command until they check out the tag.

## :seedling: New Features

- Approval asked of each participating trust, and recorded with the user who gave it — `CAN_APPROVE_FOR_TRUST` at every named trust, refused in full when one is missing (#1298).
- `TRUST_OWNER`, trust-scoped permissions, and the Trust Owner row in the Trust control panel (#1266).
- The optional trust governance document, with `[disclosure]`, `[access]` and `[fl_privacy]` sections, and a `make -C trust check-governance` target that runs the service's own loader rather than a second implementation (#1297).
- `Release checks` in this file and in the pre-release checklist: the tutorial suite on both backends, then `make e2e_smoke` (#1306).
- A DICOM service of its own on Helm trusts, a network policy for the PACS hop, and a fixed node port option (#1234).

## :bug: Bug Fixes

<!-- Update this section if a fix lands before the cut. -->

- No user-facing bug fixes in this release.

## :white_check_mark: Release checks

<!-- The gates that cannot run in CI: no GitHub-hosted runner has a GPU, and both the suite and the smoke test need real hardware and a full stack. Tick these on the release branch before the tag is cut — this section is the record that the published examples and the platform path were run, and it is shared by both release trains. See *Pre-release checklist* in CONTRIBUTING.md. -->

Tutorial suite, on a GPU host:

- [ ] NVFLARE — `make -C fl-tutorials run-all-tutorials`
- [ ] Flower — `make -C fl-tutorials run-all-tutorials FL_BACKEND=flower`
- [ ] Host and date recorded: <!-- e.g. "RTX 5090 workstation, 24 September 2026" -->

Full-platform smoke test, against a running deployment:

- [ ] NVFLARE — `make e2e_smoke`
- [ ] Flower — `make e2e_smoke FL_BACKEND=flower`

## :file_folder: PRs merged in this release

<!-- auto-populated by the release workflow -->

## :star: Acknowledgements

A big thank you to the following contributors for their work on this release:

<!-- auto-populated by the release workflow -->

---
